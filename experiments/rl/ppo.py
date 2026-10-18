"""Cold-start CPU PPO with captured candidate sets and termination-aware GAE.

Run from repository root: python3 -m experiments.rl.ppo --out results/rl/ppo
Each rollout is updated before collecting again; minibatches contain variable
candidate sets, never padded pseudo-actions or a stable action-index head.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time

import torch
from torch.distributions import Categorical

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.rl.common import ActorCritic, finish_training, make_env, make_parser, seed_all, tensorize


@dataclass
class Transition:
    obs: torch.Tensor
    candidates: torch.Tensor
    index: int
    log_prob: float
    value: float
    reward: float
    next_value: float
    terminated: bool
    truncated: bool


def compute_gae(rewards, values, next_values, terminated, truncated, gamma=.99, lam=.95):
    """Bootstrap time limits, but cut the trace across EVERY reset boundary.

    next_values are captured from the actual post-transition observation, before
    resetting the environment. A rollout boundary bootstraps just like a cut.
    """
    count = len(rewards)
    if any(len(x) != count for x in (values, next_values, terminated, truncated)):
        raise ValueError('GAE arrays must align')
    advantages = torch.empty(count, dtype=torch.float32)
    tail = 0.
    for i in reversed(range(count)):
        delta = float(rewards[i]) + gamma * (not terminated[i]) * float(next_values[i]) - float(values[i])
        tail = delta + gamma * lam * (not (terminated[i] or truncated[i])) * tail
        advantages[i] = tail
    return advantages, advantages + torch.as_tensor(values, dtype=torch.float32)


def clipped_objective(new_log_prob, old_log_prob, advantages, clip=.2):
    ratio = torch.exp(new_log_prob-old_log_prob)
    objective = torch.minimum(ratio*advantages, ratio.clamp(1-clip, 1+clip)*advantages)
    return -objective.mean(), ratio


def update(model, optimizer, rollout, *, gamma=.99, lam=.95, clip=.2, epochs=4,
           batch_size=32, value_coef=.5, entropy_coef=.01, max_grad_norm=.5, deadline=None):
    if not rollout:
        return {'updates': 0}
    advantages, returns = compute_gae([t.reward for t in rollout], [t.value for t in rollout],
        [t.next_value for t in rollout], [t.terminated for t in rollout], [t.truncated for t in rollout], gamma, lam)
    if len(advantages) > 1:
        advantages = (advantages-advantages.mean()) / advantages.std(unbiased=False).clamp_min(1e-8)
    metrics = []
    for _ in range(epochs):
        order = torch.randperm(len(rollout)).tolist()
        for start in range(0, len(order), batch_size):
            if deadline is not None and time.monotonic() >= deadline:
                break
            indices = order[start:start+batch_size]
            log_probs, values, entropies = [], [], []
            for idx in indices:
                t = rollout[idx]
                logits, value = model(t.obs, t.candidates)
                distribution = Categorical(logits=logits)
                log_probs.append(distribution.log_prob(torch.tensor(t.index)))
                values.append(value)
                entropies.append(distribution.entropy())
            new_log = torch.stack(log_probs)
            old_log = torch.tensor([rollout[i].log_prob for i in indices])
            policy_loss, ratio = clipped_objective(new_log, old_log, advantages[indices], clip)
            value_loss = .5 * (torch.stack(values)-returns[indices]).square().mean()
            entropy = torch.stack(entropies).mean()
            loss = policy_loss + value_coef*value_loss - entropy_coef*entropy
            if not torch.isfinite(loss):
                raise FloatingPointError('Nonfinite PPO loss')
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm, error_if_nonfinite=True)
            optimizer.step()
            with torch.no_grad():
                metrics.append({'loss': float(loss), 'policy_loss': float(policy_loss), 'value_loss': float(value_loss),
                    'entropy': float(entropy), 'gradient_norm': float(norm),
                    'approx_kl': float(((ratio-1)-(new_log-old_log)).mean()),
                    'clip_fraction': float(((ratio-1).abs() > clip).float().mean())})
        if deadline is not None and time.monotonic() >= deadline:
            break
    return {'updates': len(metrics), **({key: sum(m[key] for m in metrics)/len(metrics) for key in metrics[0]} if metrics else {})}


def train(args):
    if args.steps < 1 or args.max_wall <= 0 or args.rollout_steps < 1 or args.epochs < 1 or args.batch_size < 1:
        raise ValueError('Training budgets and update sizes must be positive')
    if not 0 <= args.gae_lambda <= 1 or not 0 < args.clip < 1:
        raise ValueError('Invalid PPO lambda/clip')
    seed_all(args.train_seed)
    start = time.monotonic()
    model = ActorCritic()
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    initial = torch.cat([p.detach().flatten().clone() for p in model.parameters()])
    env = make_env(args)
    env.deadline = start + args.max_wall
    summary = {'algorithm': 'ppo', 'config': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
               'reward_mode': args.reward, 'gamma': args.gamma, 'actual_steps': 0, 'optimizer_updates': 0,
               'completed_games': [], 'failed_games': [], 'truncated_games': [], 'actual_game_seeds': [], 'metrics': []}
    decision = None
    rollout = []
    episode_return = 0.
    try:
        while summary['actual_steps'] < args.steps and time.monotonic() < env.deadline:
            if decision is None:
                try:
                    decision = env.reset()
                    summary['actual_game_seeds'].append(env.seed)
                    episode_return = 0.
                except Exception as exc:
                    summary['failed_games'].append({'seed': env.seed, 'error': f'{type(exc).__name__}: {exc}', 'steps': 0})
                    # Infrastructure failure is not a sequence of legitimate defeats.
                    break
            obs, candidates = tensorize(decision)
            with torch.no_grad():
                logits, value = model(obs, candidates)
                distribution = Categorical(logits=logits)
                action = distribution.sample()
                old_log = distribution.log_prob(action)
            next_decision, reward, terminated, truncated, info = env.step(int(action))
            summary['actual_steps'] += 1
            episode_return += reward
            with torch.no_grad():
                # Technical failures have no reliable successor; do not fit a fictitious loss.
                next_value = 0. if terminated else float(model(*tensorize(next_decision))[1])
            if not info.get('technical_failure'):
                rollout.append(Transition(obs.clone(), candidates.clone(), int(action), float(old_log), float(value),
                                          reward, next_value, terminated, truncated))
            elif rollout:
                # Disconnect the GAE trace from a subsequent reset after a failed request.
                rollout[-1].truncated = True
            if terminated or truncated:
                record = {'seed': info['seed'], 'steps': info['steps'], 'return': episode_return,
                          'reason': info.get('reason'), 'outcome': info.get('outcome')}
                bucket = 'completed_games' if terminated else 'failed_games' if info.get('technical_failure') else 'truncated_games'
                summary[bucket].append(record)
                decision = None
            else:
                decision = next_decision
            if len(rollout) >= args.rollout_steps or summary['actual_steps'] >= args.steps or time.monotonic() >= env.deadline:
                stats = update(model, optimizer, rollout, gamma=args.gamma, lam=args.gae_lambda, clip=args.clip,
                               epochs=args.epochs, batch_size=args.batch_size, entropy_coef=args.entropy_coef, deadline=env.deadline)
                summary['optimizer_updates'] += stats['updates']
                summary['metrics'].append(stats)
                rollout.clear()
            if info.get('technical_failure'):
                break
        if rollout:
            stats = update(model, optimizer, rollout, gamma=args.gamma, lam=args.gae_lambda, clip=args.clip,
                           epochs=args.epochs, batch_size=args.batch_size, entropy_coef=args.entropy_coef, deadline=env.deadline)
            summary['optimizer_updates'] += stats['updates']
            summary['metrics'].append(stats)
        if decision is not None:
            summary['truncated_games'].append({'seed': env.seed, 'steps': env.steps, 'return': episode_return,
                'reason': 'max_wall' if time.monotonic() >= env.deadline else 'training_steps', 'outcome': None})
    finally:
        env.close()
    final = torch.cat([p.detach().flatten() for p in model.parameters()])
    summary.update(elapsed_seconds=time.monotonic()-start, episodes=len(summary['actual_game_seeds']),
                   completed_episodes=len(summary['completed_games']), parameter_l2_change=float(torch.linalg.vector_norm(final-initial)),
                   parameters_finite=bool(torch.isfinite(final).all()),
                   stop_reason='max_wall' if time.monotonic() >= env.deadline else 'technical_failure' if summary['failed_games'] else 'steps')
    paths = finish_training(args.out, model, summary)
    print(json.dumps({'paths': paths, 'actual_steps': summary['actual_steps'],
                      'completed_episodes': summary['completed_episodes'], 'optimizer_updates': summary['optimizer_updates']}))
    return summary


def main():
    parser = make_parser(__doc__)
    parser.add_argument('--rollout-steps', type=int, default=128, help='On-policy steps per update')
    parser.add_argument('--epochs', type=int, default=4, help='PPO passes per rollout')
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--lr', type=float, default=3e-4)
    parser.add_argument('--clip', type=float, default=.2)
    parser.add_argument('--gae-lambda', type=float, default=.95)
    parser.add_argument('--entropy-coef', type=float, default=.01)
    train(parser.parse_args())


if __name__ == '__main__':
    main()
