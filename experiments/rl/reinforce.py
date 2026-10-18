"""REINFORCE with learned state-value baseline for 卫戍协议 ABYSS.

Episodic Monte Carlo policy gradients over the shared candidate scorer
(common.ActorCritic). One optimizer update per finished episode.

Truncation policy: episodes ended by the step/time limit (truncated=True,
not adapter-terminal) are *bootstrapped* with the learned value of the final
observed state, V(s_T) (evaluated with torch.no_grad on the exact tensorized
final decision; 0.0 if the final decision is terminal/candidate-less or
tensorize fails). Bootstrapping keeps Monte Carlo targets finite for partial
episodes, but truncated episodes are counted in their own counter and are
NEVER counted as wins/losses or "completed" episodes.

Entropy bonus: justified for cold-start Monte Carlo (no expert data) —
a small coefficient (default 0.01) prevents premature collapse of the
candidate-scorer distribution; logged every update.

Learner elimination is not team terminal: we rely on the env's
terminated flag (adapter state.terminal only), so a dead learner seat does
not end the episode and cannot fabricate a win/loss.
"""

import argparse
import math
import os
import sys
import time

import torch

try:  # common.py is parent-owned and may still be under construction.
    from common import (
        ACTION_DIM,
        OBS_DIM,
        ActorCritic,
        Decision,
        PlayerEnv,
        finish_training,
        make_env,
        make_parser,
        seed_all,
        tensorize,
    )
except ImportError:  # keep this file importable for unit tests
    common = None
else:
    common = sys.modules["common"]


def returns(rewards, gamma, bootstrap=0.0):
    """Discounted Monte Carlo returns, right-to-left.

    bootstrap is the value estimate of the state after the last reward
    (used only for truncated episodes); 0.0 for naturally terminated
    episodes (Phi=0 at true terminal per the shared reward contract).
    """
    g = float(bootstrap)
    out = [0.0] * len(rewards)
    for i in range(len(rewards) - 1, -1, -1):
        g = rewards[i] + gamma * g
        out[i] = g
    return out


def classify_outcome(terminated, truncated, terminal_reward, victory=None):
    """Win/loss only for naturally terminated episodes.

    Truncated (or failed) episodes return None: they are never counted
    as wins, regardless of the sign of the partial return. ``victory``
    (adapter outcome, passed when available) takes precedence over the
    reward sign, since the terminal reward mixes in -potential(prev).
    """
    if truncated or not terminated:
        return None
    if victory is not None:
        return "win" if victory else "loss"
    return "win" if terminal_reward > 0 else "loss"


def sample_action(model, obs_t, act_t):
    """Sample one candidate index; keep exact captured features and graph.

    Returns the chosen index, its logprob (with grad), the entropy of the
    candidate distribution (with grad), and the state value (with grad).
    Candidate count N is dynamic: logits length always equals act_t rows.
    """
    logits, value = model(obs_t, act_t)
    dist = torch.distributions.Categorical(logits=logits)
    index = dist.sample()
    return {
        "index": int(index.item()),
        "logprob": dist.log_prob(index),
        "entropy": dist.entropy(),
        "value": value.reshape(()),
    }


def bootstrap_value(model, decision):
    """Value of the final state of a truncated episode, for bootstrapping.

    No grad. 0.0 when the final decision is terminal, candidate-less, or
    tensorization fails for any reason.
    """
    if decision is None or getattr(decision, "terminal", False):
        return 0.0
    features = getattr(decision, "action_features", None)
    if not features:
        return 0.0
    try:
        obs_t, act_t = tensorize(decision)
        with torch.no_grad():
            _, value = model(obs_t, act_t)
        v = float(value.reshape(()).item())
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def run_episode(env, model, decision, max_steps_hint=None):
    """Roll out one episode until terminated/truncated/no candidates.

    Stores per-step logprob/value/entropy tensors (exact captured features;
    graphs kept for the single end-of-episode backward) and raw rewards.
    """
    ep = {
        "logprobs": [],
        "values": [],
        "entropies": [],
        "rewards": [],
        "steps": 0,
        "terminated": False,
        "truncated": False,
        "failed": False,
        "bootstrap": 0.0,
        "error": None,
        "info": {},
    }
    while True:
        if decision is None or getattr(decision, "terminal", False):
            ep["terminated"] = bool(decision is not None and getattr(decision, "terminal", False))
            break
        features = getattr(decision, "action_features", None)
        if not features:
            # No candidates but not terminal: treat as technical break,
            # not as a completed episode.
            ep["failed"] = True
            ep["error"] = "decision without candidates and not terminal"
            break
        obs_t, act_t = tensorize(decision)
        choice = sample_action(model, obs_t, act_t)
        ep["logprobs"].append(choice["logprob"])
        ep["values"].append(choice["value"])
        ep["entropies"].append(choice["entropy"])
        try:
            next_decision, reward, terminated, truncated, info = env.step(choice["index"])
        except Exception as exc:  # technical failure, distinct from game loss
            ep["failed"] = True
            ep["error"] = f"{type(exc).__name__}: {exc}"
            ep["logprobs"].pop()
            ep["values"].pop()
            ep["entropies"].pop()
            break
        ep["rewards"].append(float(reward))
        ep["steps"] += 1
        ep["info"] = info if isinstance(info, dict) else {}
        if terminated:
            ep["terminated"] = True
            break
        if truncated:
            if ep["info"].get("technical_failure"):
                # Exception/timeout: distinct from a legitimate truncation;
                # the partial episode is discarded (no update, not a win).
                ep["failed"] = True
                ep["error"] = ep["info"].get("reason")
                ep["truncated"] = False
                for key in ("logprobs", "values", "entropies"):
                    ep[key].pop()
                ep["rewards"].pop()
                ep["steps"] -= 1
                break
            ep["truncated"] = True
            ep["bootstrap"] = bootstrap_value(model, next_decision)
            break
        decision = next_decision
    return ep


def update(model, optimizer, ep, gamma, value_coef=0.5, entropy_coef=0.01,
           grad_clip=5.0):
    """One REINFORCE update: policy grad with state-value baseline + critic MSE.

    loss = mean( -logpi * (G - V(s)).detach() ) + value_coef * MSE(G, V)
           - entropy_coef * mean(entropy)
    Truncated episodes pass ep['bootstrap'] = V(s_T) into the return target.
    Returns a dict of scalar diagnostics; None if the episode had no steps.
    """
    if ep["steps"] == 0:
        return None
    returns_t = torch.tensor(
        returns(ep["rewards"], gamma, bootstrap=ep["bootstrap"]),
        dtype=torch.float32,
    )
    logprobs = torch.stack(ep["logprobs"])
    values = torch.stack(ep["values"])
    entropies = torch.stack(ep["entropies"])
    advantages = returns_t - values
    policy_loss = -(logprobs * advantages.detach()).mean()
    value_loss = advantages.pow(2).mean()
    entropy = entropies.mean()
    loss = policy_loss + value_coef * value_loss - entropy_coef * entropy
    optimizer.zero_grad()
    loss.backward()
    finite_grads = all(
        torch.isfinite(p.grad).all().item()
        for p in model.parameters()
        if p.grad is not None
    )
    if grad_clip is not None:
        torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
    optimizer.step()
    return {
        "loss": float(loss.item()),
        "policy_loss": float(policy_loss.item()),
        "value_loss": float(value_loss.item()),
        "entropy": float(entropy.item()),
        "mean_return": float(returns_t.mean().item()),
        "finite_grads": bool(finite_grads),
    }


def param_l2(model):
    with torch.no_grad():
        return float(sum(p.pow(2).sum().item() for p in model.parameters()))


def actual_seed(env, decision):
    """Best-effort extraction of the actual game seed for training metadata."""
    candidates = (
        getattr(env, "info", None),
        getattr(decision, "info", None),
        getattr(env, "last_info", None),
    )
    for src in candidates:
        if isinstance(src, dict) and "seed" in src:
            return src["seed"]
    for src in (env, decision):
        seed = getattr(src, "seed", None)
        if seed is not None and not callable(seed):
            return seed
    return None


def main(argv=None):
    if common is None:
        raise SystemExit(
            "common.py is not available next to reinforce.py; "
            "implement the shared contract in experiments/rl/common.py first."
        )
    parser = make_parser(
        "REINFORCE with learned state-value baseline for 卫戍协议 ABYSS"
    )
    parser.add_argument("--lr", type=float, default=3e-4,
                        help="Adam learning rate (default 3e-4).")
    parser.add_argument("--value-coef", type=float, default=0.5,
                        help="Weight of the state-value MSE baseline loss.")
    parser.add_argument("--entropy-coef", type=float, default=0.01,
                        help="Small entropy bonus: cold-start Monte Carlo "
                             "exploration, prevents premature collapse.")
    parser.add_argument("--grad-clip", type=float, default=5.0,
                        help="Global grad-norm clip for MC variance.")
    args = parser.parse_args(argv)

    seed_all(args.train_seed)
    env = make_env(args)
    model = ActorCritic()
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    t0 = time.time()
    steps_used = 0
    episodes_completed = 0
    episodes_truncated = 0
    episodes_failed = 0
    wins = 0
    losses = 0
    optimizer_updates = 0
    finite_grad_updates = 0
    game_seeds = []
    completed_returns = []
    history = []
    l2_before = param_l2(model)

    try:
        while steps_used < args.steps and time.time() - t0 < args.max_wall:
            try:
                decision = env.reset()
            except Exception as exc:
                episodes_failed += 1
                print(f"[reinforce] reset failed: {type(exc).__name__}: {exc}",
                      flush=True)
                break
            seed = actual_seed(env, decision)
            if seed is not None:
                game_seeds.append(seed)
            ep = run_episode(env, model, decision,
                             max_steps_hint=args.max_episode_steps)
            steps_used += ep["steps"]
            upd = None
            if ep["steps"] > 0 and not ep["failed"]:
                upd = update(
                    model, optimizer, ep, args.gamma,
                    value_coef=args.value_coef,
                    entropy_coef=args.entropy_coef,
                    grad_clip=args.grad_clip,
                )
                if upd is not None:
                    optimizer_updates += 1
                    history.append(upd)
                    if upd["finite_grads"]:
                        finite_grad_updates += 1
            terminal_reward = ep["rewards"][-1] if ep["rewards"] else 0.0
            victory = None
            if ep["terminated"]:
                outcome_info = ep["info"].get("outcome") or {}
                victory = (outcome_info.get("team") or {}).get("victory")
            outcome = classify_outcome(
                ep["terminated"], ep["truncated"], terminal_reward,
                victory=victory)
            if ep["failed"]:
                episodes_failed += 1
            elif ep["truncated"]:
                episodes_truncated += 1
            else:
                episodes_completed += 1
                completed_returns.append(sum(ep["rewards"]))
            if outcome == "win":
                wins += 1
            elif outcome == "loss":
                losses += 1
            line = (
                f"[reinforce] episodes={episodes_completed + episodes_truncated} "
                f"steps={steps_used}/{args.steps} "
                f"done={episodes_completed} trunc={episodes_truncated} "
                f"failed={episodes_failed} wins={wins} "
                f"ep_return={sum(ep['rewards']):+.3f} "
                f"steps_this_ep={ep['steps']}"
            )
            if upd is not None:
                line += (
                    f" loss={upd['loss']:.4f} pi={upd['policy_loss']:.4f} "
                    f"v={upd['value_loss']:.4f} H={upd['entropy']:.4f}"
                )
            if ep["error"]:
                line += f" error={ep['error']}"
            print(line, flush=True)
    finally:
        env.close()

    elapsed = time.time() - t0
    l2_after = param_l2(model)

    def mean(key):
        return float(sum(h[key] for h in history) / len(history)) if history else 0.0

    summary = {
        "algorithm": "reinforce",
        "variant": "episodic Monte Carlo policy gradient with learned "
                   "state-value baseline",
        "reward_mode": args.reward,
        "gamma": args.gamma,
        "mode": args.mode,
        "partners": args.partners,
        "device": "cpu",
        "train_seed": args.train_seed,
        "first_game_seed": args.first_game_seed,
        "actual_steps": steps_used,
        "planned_steps": args.steps,
        "episodes_completed": episodes_completed,
        "episodes_truncated": episodes_truncated,
        "episodes_failed": episodes_failed,
        "completed_wins": wins,
        "completed_losses": losses,
        "truncated_never_counted_as_wins": True,
        "truncation_handling": (
            "truncated episodes are bootstrapped with V(s_T) of the final "
            "tensorized decision (0.0 if unavailable) for the Monte Carlo "
            "return target; they are counted separately from completed "
            "episodes and never reported as wins/losses"
        ),
        "optimizer_updates": optimizer_updates,
        "optimizer": "Adam",
        "lr": args.lr,
        "value_coef": args.value_coef,
        "entropy_coef": args.entropy_coef,
        "entropy_justification": "cold-start MC exploration; small coefficient",
        "grad_clip": args.grad_clip,
        "finite_grad_updates": finite_grad_updates,
        "param_l2_before": l2_before,
        "param_l2_after": l2_after,
        "param_l2_delta": l2_after - l2_before,
        "mean_loss": mean("loss"),
        "mean_policy_loss": mean("policy_loss"),
        "mean_value_loss": mean("value_loss"),
        "mean_entropy": mean("entropy"),
        "mean_episode_return_completed": (
            float(sum(completed_returns) / len(completed_returns))
            if completed_returns else 0.0
        ),
        "game_seeds": game_seeds,
        "elapsed_seconds": elapsed,
        "wall_limit_seconds": args.max_wall,
        "max_episode_steps": args.max_episode_steps,
    }
    paths = finish_training(args.out, model, summary)
    print(f"[reinforce] finished in {elapsed:.1f}s: {paths}", flush=True)
    return paths


if __name__ == "__main__":
    main()
