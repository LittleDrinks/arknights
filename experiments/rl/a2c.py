"""Independent A2C (n-step synchronous actor-critic) for 卫戍协议 ABYSS.

Imports the shared experiment contract from common.py (owned by ark-sol-ppo):
OBS_DIM/ACTION_DIM, Decision, tensorize, ActorCritic, PlayerEnv, seed_all,
make_parser, make_env, finish_training. Nothing here duplicates the
environment, featurization, or export infrastructure.

A2C-specific math (n-step returns, loss, update) lives here and is
unit-tested without the game or the shared module.

Semantics:
- Stochastic policy: softmax over per-candidate logits; the candidate list is
  re-evaluated per decision from exact captured features, so candidate index
  has no cross-state meaning.
- n-step returns with bootstrap: nonterminal chains bootstrap V(s_{t+n});
  truncation (max-episode-steps, wall budget, technical failure) bootstraps
  V(next decision); only a true terminal (adapter state.terminal) bootstraps 0.
  Learner-seat elimination is NOT team terminal (shared env guarantees this).
- Reward comes from the shared env (sparse or potential); gamma is the same.
- Env exceptions are technical failures: counted separately, never counted as
  a completed game or a legitimate loss.
"""
from __future__ import annotations

import importlib
import math
import sys
import time
from pathlib import Path

import torch


def _common():
    """Resolve the shared contract module (injected fake wins for tests)."""
    if "common" in sys.modules:
        return sys.modules["common"]
    # common.py imports arknights.adapter, so the checkout root must be importable.
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    for name in ("common", "experiments.rl.common"):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError(
        "shared contract module common.py not found (experiments/rl/common.py, "
        "owned by ark-sol-ppo); a2c.py imports against it and does not reimplement it")


def compute_nstep_returns(rewards, boots, terminated, truncated, gamma):
    """Exact n-step returns with correct terminal/truncated handling.

    rewards[t]: env reward at step t.
    boots[t]: bootstrap value V(next state) if step t does not chain to t+1
              (episode boundary inside the window, last step of the window,
              or truncation); unused when terminated[t].
    terminated[t]: true adapter terminal -> zero bootstrap.
    truncated[t]: time/step/technical cut -> bootstrap boots[t].

    R_t = r_t + gamma * (0                    if terminated[t]
                         boots[t]             if truncated[t] or t == last
                         R_{t+1}              otherwise)
    """
    n = len(rewards)
    returns = [0.0] * n
    acc = 0.0
    for t in range(n - 1, -1, -1):
        if terminated[t]:
            contribution = 0.0
        elif truncated[t] or t == n - 1:
            contribution = float(boots[t])
        else:
            contribution = acc
        returns[t] = float(rewards[t]) + gamma * contribution
        acc = returns[t]
    return returns


def decision_logp_entropy(logits):
    """log-probabilities and entropy of the categorical over candidates."""
    logp = torch.log_softmax(logits, dim=0)
    probs = logp.exp()
    entropy = -(probs * logp).sum()
    return logp, probs, entropy


def a2c_update(model, optimizer, batch, *, gamma, vf_coef, ent_coef, max_grad_norm):
    """One synchronous A2C update over an n-step batch.

    batch entries: {"obs": [OBS_DIM], "actions": [N, ACTION_DIM],
                    "index": int, "return": float}. Candidates are
    re-scored from the exact captured feature rows (index is only a
    position inside this decision, never a stable action identity).

    Returns {"policy_loss", "value_loss", "entropy", "grad_norm"} or None if
    the combined loss was non-finite (update skipped, never backpropagated).
    """
    policy_terms, value_terms, entropy_terms = [], [], []
    for entry in batch:
        obs = entry["obs"]
        actions = entry["actions"]
        logits, value = model(obs, actions)
        logp, _probs, entropy = decision_logp_entropy(logits)
        logp_chosen = logp[entry["index"]]
        returns = torch.tensor(float(entry["return"]), dtype=torch.float32)
        advantage = returns - value
        policy_terms.append(-logp_chosen * advantage.detach())
        value_terms.append((returns - value) ** 2)
        entropy_terms.append(entropy)

    policy_loss = torch.stack(policy_terms).mean()
    value_loss = torch.stack(value_terms).mean()
    entropy = torch.stack(entropy_terms).mean()
    loss = policy_loss + vf_coef * value_loss - ent_coef * entropy

    if not torch.isfinite(loss):
        return None

    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    grad_norm = torch.nn.utils.clip_grad_norm_(
        model.parameters(), max_norm=max_grad_norm)
    if not torch.isfinite(grad_norm):
        optimizer.zero_grad(set_to_none=True)
        return None
    optimizer.step()

    return {
        "policy_loss": float(policy_loss.detach()),
        "value_loss": float(value_loss.detach()),
        "entropy": float(entropy.detach()),
        "grad_norm": float(grad_norm),
    }


def _decision_actionable(decision):
    return (not decision.terminal) and len(decision.actions) > 0


def _bootstrap_value(common, model, decision):
    """V(next decision) for truncation/window bootstrap; 0 for true terminal
    or a next decision with no candidates."""
    if decision is None or decision.terminal or len(decision.actions) == 0:
        return 0.0
    obs_t, act_t = common.tensorize(decision)
    with torch.no_grad():
        _logits, value = model(obs_t, act_t)
    return float(value)


def _episode_result(info, reward):
    """(win, loss) from the true team outcome, independent of reward shaping."""
    outcome = info.get("outcome") if isinstance(info, dict) else None
    victory = outcome.get("team", {}).get("victory") if isinstance(outcome, dict) else None
    if victory is None:
        victory = reward > 0
    return bool(victory), not bool(victory)


def train(args):
    common = _common()
    common.seed_all(args.train_seed)

    model = common.ActorCritic()
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    param_before = {name: p.detach().clone()
                    for name, p in model.named_parameters()}

    started = time.monotonic()
    env = common.make_env(args)
    decision = env.reset(seed=args.first_game_seed)

    stats = {
        "steps": 0,
        "updates": 0,
        "skipped_updates": 0,
        "completed": 0,
        "truncated": 0,
        "failures": 0,
        "wins": 0,
        "losses": 0,
        "seeds": [],
        "policy_loss_sum": 0.0,
        "value_loss_sum": 0.0,
        "entropy_sum": 0.0,
        "entropy_n": 0,
        "last_grad_norm": 0.0,
    }

    def out_of_budget():
        return (stats["steps"] >= args.steps
                or time.monotonic() - started >= args.max_wall)

    while stats["steps"] < args.steps and time.monotonic() - started < args.max_wall:
        batch = []
        for _ in range(max(1, args.n_step)):
            if out_of_budget():
                break
            if not _decision_actionable(decision):
                # Nonterminal decision without candidates should not happen;
                # treat as a technical failure, never as a game result.
                stats["failures"] += 1
                try:
                    decision = env.reset()
                except Exception:
                    break
                if not _decision_actionable(decision):
                    break
                continue

            obs_t, act_t = common.tensorize(decision)
            with torch.no_grad():
                logits, _value = model(obs_t, act_t)
                logp, probs, _entropy = decision_logp_entropy(logits)
                index = int(torch.multinomial(probs, 1))

            next_decision = None
            try:
                next_decision, reward, terminated, truncated, info = env.step(index)
            except Exception:
                stats["failures"] += 1
                terminated, truncated, reward = False, True, 0.0
                info = {}
                try:
                    next_decision = env.reset()
                except Exception:
                    next_decision = None
                    break

            stats["steps"] += 1
            seed = info.get("seed") if isinstance(info, dict) else None
            if seed is not None and (not stats["seeds"] or stats["seeds"][-1] != seed):
                stats["seeds"].append(seed)
            if isinstance(info, dict) and info.get("technical_failure"):
                stats["failures"] += 1

            entry = {
                "obs": obs_t.detach(),
                "actions": act_t.detach(),
                "index": index,
                "reward": float(reward),
                "terminated": bool(terminated),
                "truncated": bool(truncated),
                "next_decision": None,
            }
            batch.append(entry)

            if terminated:
                stats["completed"] += 1
                win, loss = _episode_result(info, reward)
                stats["wins"] += win
                stats["losses"] += loss
                try:
                    decision = env.reset()
                except Exception:
                    break
            elif truncated:
                stats["truncated"] += 1
                entry["next_decision"] = next_decision
                try:
                    decision = env.reset()
                except Exception:
                    break
            else:
                entry["next_decision"] = next_decision
                decision = next_decision

        if not batch:
            break

        last = len(batch) - 1
        boots = []
        for i, entry in enumerate(batch):
            if entry["terminated"]:
                boots.append(0.0)
            elif entry["truncated"] or i == last:
                boots.append(_bootstrap_value(common, model,
                                              entry["next_decision"]))
            else:
                boots.append(0.0)  # unused: chains to R_{t+1}

        returns = compute_nstep_returns(
            [e["reward"] for e in batch],
            boots,
            [e["terminated"] for e in batch],
            [e["truncated"] for e in batch],
            args.gamma,
        )
        for entry, value in zip(batch, returns):
            entry["return"] = value

        outcome = a2c_update(model, optimizer, batch,
                             gamma=args.gamma,
                             vf_coef=args.vf_coef,
                             ent_coef=args.ent_coef,
                             max_grad_norm=args.max_grad_norm)
        if outcome is None:
            stats["skipped_updates"] += 1
        else:
            stats["updates"] += 1
            stats["policy_loss_sum"] += outcome["policy_loss"]
            stats["value_loss_sum"] += outcome["value_loss"]
            stats["entropy_sum"] += outcome["entropy"]
            stats["entropy_n"] += 1
            stats["last_grad_norm"] = outcome["grad_norm"]

    env.close()
    elapsed = time.monotonic() - started

    param_after = {name: p.detach().clone() for name, p in model.named_parameters()}
    changed, max_diff = 0, 0.0
    for name, before in param_before.items():
        diff = float((param_after[name] - before).abs().max())
        max_diff = max(max_diff, diff)
        if diff > 0.0:
            changed += 1

    updates = max(1, stats["updates"])
    summary = {
        "algorithm": "a2c",
        "task": "n-step synchronous actor-critic",
        "obs_dim": int(common.OBS_DIM),
        "action_dim": int(common.ACTION_DIM),
        "difficulty": "ABYSS",
        "mode": args.mode,
        "partners": args.partners,
        "reward_mode": args.reward,
        "gamma": args.gamma,
        "train_seed": args.train_seed,
        "first_game_seed": args.first_game_seed,
        "games_played": stats["completed"] + stats["truncated"] + stats["failures"],
        "game_seeds_first_last_count": [
            stats["seeds"][0] if stats["seeds"] else None,
            stats["seeds"][-1] if stats["seeds"] else None,
            len(stats["seeds"]),
        ],
        "hyperparameters": {
            "n_step": args.n_step,
            "lr": args.lr,
            "ent_coef": args.ent_coef,
            "vf_coef": args.vf_coef,
            "max_grad_norm": args.max_grad_norm,
            "max_episode_steps": args.max_episode_steps,
        },
        "steps": stats["steps"],
        "episodes_completed": stats["completed"],
        "episodes_truncated": stats["truncated"],
        "technical_failures": stats["failures"],
        "learner_wins": stats["wins"],
        "learner_losses": stats["losses"],
        "optimizer_updates": stats["updates"],
        "skipped_updates": stats["skipped_updates"],
        "losses": {
            "policy_mean": stats["policy_loss_sum"] / updates,
            "value_mean": stats["value_loss_sum"] / updates,
            "entropy_mean": stats["entropy_sum"] / updates,
            "last_grad_norm": stats["last_grad_norm"],
        },
        "elapsed_seconds": elapsed,
        "parameter_change": {
            "params_total": len(param_before),
            "params_changed": changed,
            "max_abs_diff": max_diff,
            "nonzero": changed > 0,
        },
        "notes": (
            "terminal bootstrap 0 only on true adapter terminal; truncation "
            "(max steps/wall/technical failure) bootstraps V(next decision); "
            "learner elimination is not team terminal; env exceptions are "
            "technical failures, not legitimate losses"
        ),
    }

    paths = common.finish_training(args.out, model, summary)
    print(f"[a2c] steps={stats['steps']} completed={stats['completed']} "
          f"truncated={stats['truncated']} failures={stats['failures']} "
          f"updates={stats['updates']} wins={stats['wins']} "
          f"losses={stats['losses']} elapsed={elapsed:.1f}s -> {paths}")
    return paths


def main():
    common = _common()
    parser = common.make_parser("A2C n-step synchronous actor-critic for 卫戍协议 ABYSS")
    parser.add_argument("--n-step", type=int, default=16,
                        help="rollout window between synchronous updates")
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--ent-coef", type=float, default=0.01)
    parser.add_argument("--vf-coef", type=float, default=0.5)
    parser.add_argument("--max-grad-norm", type=float, default=0.5)
    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
