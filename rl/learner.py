#!/usr/bin/env python3
"""CPU-only BC and masked PPO over a persistent JSONL engine subprocess; no Gym/SB3."""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
import argparse
import hashlib
import json
import math
from pathlib import Path
import selectors
import shlex
import subprocess
import time

import torch
from torch import nn
from torch.distributions import Categorical

torch.set_num_threads(1)
torch.set_num_interop_threads(1)
HERE = Path(__file__).resolve().parent
DEFAULT_ENV = f'node {shlex.quote(str(HERE / "env.mjs"))}'


class Environment:
    def __init__(self, command=DEFAULT_ENV, timeout=120):
        self.process = subprocess.Popen(shlex.split(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=None, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.timeout = timeout
        self.buffer = bytearray()

    def request(self, cmd, **kwargs):
        self.process.stdin.write((json.dumps({'cmd': cmd, **kwargs}) + '\n').encode())
        deadline = time.monotonic() + self.timeout
        while b'\n' not in self.buffer:
            if not self.selector.select(max(0, deadline - time.monotonic())):
                raise TimeoutError(f'Environment timeout in {cmd}')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError(f'Environment exited: {self.process.poll()}')
            self.buffer.extend(chunk)
            if len(self.buffer) > 64 * 1024 * 1024:
                raise RuntimeError('Environment response exceeds 64 MiB')
        line, _, rest = self.buffer.partition(b'\n')
        self.buffer = bytearray(rest)
        response = json.loads(line)
        if not response.get('ok'):
            raise RuntimeError(response)
        if response.get('engineErrors') or response.get('actionErrors'):
            raise RuntimeError(f'Invalid rollout: {response}')
        return response

    def close(self):
        try:
            if self.process.poll() is None:
                self.process.stdin.write(b'{"cmd":"close"}\n')
                self.process.wait(timeout=2)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        finally:
            self.selector.close()
            self.process.stdin.close()
            self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ActorCritic(nn.Module):
    def __init__(self, global_dim, candidate_dim, hidden=64):
        super().__init__()
        self.dimensions = [global_dim, candidate_dim, hidden]
        self.state = nn.Sequential(nn.Linear(global_dim, hidden), nn.Tanh())
        self.actor = nn.Sequential(nn.Linear(hidden + candidate_dim, hidden), nn.Tanh(), nn.Linear(hidden, 1))
        self.critic = nn.Linear(hidden, 1)

    def forward(self, features):
        # Real variable-size action sets. Padding is never a legal action.
        globals_ = torch.tensor([f['global'] for f in features], dtype=torch.float32)
        width = max(len(f['candidates']) for f in features)
        if width == 0:
            raise ValueError('A terminal observation cannot be sampled')
        candidates = torch.zeros(len(features), width, self.dimensions[1])
        mask = torch.zeros(len(features), width, dtype=torch.bool)
        for i, f in enumerate(features):
            k = len(f['candidates'])
            if not k or not any(f['mask']):
                raise ValueError('No legal actions')
            candidates[i, :k] = torch.tensor(f['candidates'], dtype=torch.float32)
            mask[i, :k] = torch.tensor(f['mask'], dtype=torch.bool)
        h = self.state(globals_)
        logits = self.actor(torch.cat([h[:, None, :].expand(-1, width, -1), candidates], dim=-1)).squeeze(-1)
        return Categorical(logits=logits.masked_fill(~mask, -torch.inf)), self.critic(h).squeeze(-1)

    @torch.no_grad()
    def value(self, features):
        return self.critic(self.state(torch.tensor(features['global'], dtype=torch.float32))).item()

    @torch.no_grad()
    def act(self, features, greedy=False):
        distribution, value = self([features])
        action = distribution.logits.argmax(-1) if greedy else distribution.sample()
        return action.item(), distribution.log_prob(action).item(), value.item()


def vector(model):
    return torch.cat([p.detach().reshape(-1) for p in model.parameters()]).clone()


def digest(model):
    return hashlib.sha256(vector(model).numpy().tobytes()).hexdigest()


def save_checkpoint(path, model, schema, rules, metadata, optimizer=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({'model': model.state_dict(), 'dimensions': model.dimensions, 'schema': schema, 'rules': rules,
                'metadata': metadata, 'optimizer': optimizer.state_dict() if optimizer else None,
                'torch_rng': torch.get_rng_state()}, path)


def load_checkpoint(path):
    ckpt = torch.load(path, map_location='cpu', weights_only=True)
    model = ActorCritic(*ckpt['dimensions'])
    model.load_state_dict(ckpt['model'])
    return model, ckpt


def validate_checkpoint(ckpt, state):
    dims = [len(state['features']['global']), len(state['features']['candidates'][0])]
    if ckpt['schema'] != state['schema'] or ckpt['rules'] != state['rules'] or ckpt['dimensions'][:2] != dims:
        raise ValueError('Checkpoint feature/rule schema mismatch')


def bc_update(model, samples, epochs=2, lr=3e-4, batch_size=32):
    if not samples:
        raise ValueError('No accepted teacher samples')
    for sample in samples:
        f, a = sample['features'], sample['action']
        if not isinstance(a, int) or not 0 <= a < len(f['mask']) or not f['mask'][a]:
            raise ValueError('BC label is not a legal candidate')
    before = vector(model)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    losses = []
    for _ in range(epochs):
        for indices in torch.randperm(len(samples)).split(batch_size):
            batch = [samples[i] for i in indices.tolist()]
            distribution, _ = model([s['features'] for s in batch])
            labels = torch.tensor([s['action'] for s in batch])
            loss = -distribution.log_prob(labels).mean()
            if not torch.isfinite(loss):
                raise RuntimeError('Non-finite BC loss')
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            optimizer.step()
            losses.append(loss.item())
    return {'loss': sum(losses) / len(losses), 'updates': len(losses), 'samples': len(samples),
            'parameterDeltaL2': (vector(model) - before).norm().item(), 'parameterSHA256': digest(model)}, optimizer


def gae(rewards, dones, values, bootstrap, gamma=0.99, lam=0.95):
    advantages = torch.zeros(len(rewards))
    carry, next_value = 0.0, bootstrap
    for i in reversed(range(len(rewards))):
        live = 1.0 - float(dones[i])
        delta = rewards[i] + gamma * next_value * live - values[i]
        carry = delta + gamma * lam * live * carry
        advantages[i] = carry
        next_value = values[i]
    return advantages, advantages + torch.tensor(values)


def ppo_update(model, optimizer, rollout, bootstrap, config):
    before = vector(model)
    advantage, returns = gae(rollout['rewards'], rollout['dones'], rollout['values'], bootstrap,
                             config['gamma'], config['gae_lambda'])
    # One-sample updates retain the signal instead of centering it to zero.
    if len(advantage) > 1:
        advantage = (advantage - advantage.mean()) / (advantage.std(unbiased=False) + 1e-8)
    old = torch.tensor(rollout['logprobs'])
    actions = torch.tensor(rollout['actions'])
    metrics = []
    for _ in range(config['epochs']):
        for idx in torch.randperm(len(actions)).split(config['batch_size']):
            distribution, value = model([rollout['features'][i] for i in idx.tolist()])
            logprob = distribution.log_prob(actions[idx])
            ratio = (logprob - old[idx]).exp()
            clipped = ratio.clamp(1 - config['clip'], 1 + config['clip'])
            policy_loss = -torch.minimum(ratio * advantage[idx], clipped * advantage[idx]).mean()
            value_loss = (value - returns[idx]).square().mean()
            entropy = distribution.entropy().mean()
            loss = policy_loss + config['value_coef'] * value_loss - config['entropy_coef'] * entropy
            if not torch.isfinite(loss):
                raise RuntimeError('Non-finite PPO loss')
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), config['max_grad_norm'])
            optimizer.step()
            metrics.append([loss.item(), policy_loss.item(), value_loss.item(), entropy.item()])
    return {'loss': metrics[-1][0], 'policyLoss': metrics[-1][1], 'valueLoss': metrics[-1][2],
            'entropy': metrics[-1][3], 'updates': len(metrics), 'environmentSteps': len(actions),
            'parameterDeltaL2': (vector(model) - before).norm().item(), 'parameterSHA256': digest(model),
            'bootstrapValue': bootstrap, 'terminalTransitions': sum(rollout['dones'])}


def episode_result(state):
    return {**state['episode'], **{key: state[key] for key in ['victory', 'rounds', 'steps', 'engineErrors', 'actionErrors',
                                       'controllerErrors', 'budgetForcedReady', 'controllerBlockedSales']}}


def evaluate(env, model, seed, difficulty, budget=128, trace_path=None):
    state = env.request('reset', seed=seed, mode='solo', difficulty=difficulty, budget=budget)
    trace = []
    while not state['done']:
        action, _, _ = model.act(state['features'], greedy=True)
        trace.append({'round': state['rounds'], 'candidateIndex': action, 'action': state['candidates'][action]})
        state = env.request('step', candidateIndex=action)
    result = {'seed': seed, 'difficulty': difficulty, **episode_result(state)}
    if trace_path:
        Path(trace_path).write_text(json.dumps({'rules': state['rules'], 'controller': state['controller'],
                                              'budget': budget, 'result': result, 'actions': trace}) + '\n')
    result['actionHash'] = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
    return result


def collect_rollout(env, model, state, count, reset):
    rollout = {k: [] for k in ['features', 'actions', 'logprobs', 'values', 'rewards', 'dones']}
    episodes = []
    for _ in range(count):
        if state['done']:
            state = reset()
        action, logprob, value = model.act(state['features'])
        rollout['features'].append(state['features'])
        rollout['actions'].append(action)
        rollout['logprobs'].append(logprob)
        rollout['values'].append(value)
        state = env.request('step', candidateIndex=action)
        rollout['rewards'].append(state['reward'])
        rollout['dones'].append(state['done'])
        if state['done']:
            episodes.append(episode_result(state))
    bootstrap = 0.0 if state['done'] else model.value(state['features'])
    return rollout, bootstrap, state, episodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-command', default=DEFAULT_ENV, help='Persistent JSONL command, shlex parsed; no shell')
    parser.add_argument('--config', default=str(HERE / 'config.json'))
    parser.add_argument('--seed', type=int, default=1)
    sub = parser.add_subparsers(dest='command', required=True)
    demos = sub.add_parser('demo')
    demos.add_argument('--episodes', type=int, default=1)
    demos.add_argument('--difficulty', default='NORMAL', choices=['NORMAL', 'HARD', 'ABYSS'])
    demos.add_argument('--out', required=True)
    bc = sub.add_parser('bc')
    bc.add_argument('--data', required=True)
    bc.add_argument('--out', required=True)
    bc.add_argument('--epochs', type=int, default=2)
    bc.add_argument('--max-samples', type=int, default=1024)
    ppo = sub.add_parser('ppo')
    ppo.add_argument('--checkpoint', required=True)
    ppo.add_argument('--out', required=True)
    ppo.add_argument('--steps-per-stage', type=int, default=1024)
    ppo.add_argument('--curriculum', default='NORMAL,HARD,ABYSS')
    ev = sub.add_parser('eval')
    ev.add_argument('--checkpoint', required=True)
    ev.add_argument('--episodes', type=int, default=1)
    ev.add_argument('--difficulty', default='ABYSS', choices=['NORMAL', 'HARD', 'ABYSS'])
    ev.add_argument('--trace-dir')
    args = parser.parse_args()
    if not 1 <= args.seed <= 0xffffffff:
        parser.error('seed must be in 1..4294967295 (the actual engine seed range)')
    if hasattr(args, 'episodes') and (args.episodes < 1 or args.seed + args.episodes - 1 > 0xffffffff):
        parser.error('episodes must be positive and the full seed range must fit uint32')
    torch.manual_seed(args.seed)
    config = json.loads(Path(args.config).read_text())
    start = time.monotonic()
    if args.command == 'bc':
        samples, header = [], None
        with open(args.data) as stream:
            for line in stream:
                row = json.loads(line)
                if row['type'] == 'metadata':
                    header = row
                elif row['type'] == 'sample' and len(samples) < args.max_samples:
                    samples.append(row)
        if not header or not samples:
            raise ValueError('Expected metadata and legal samples from demo CLI')
        f = samples[0]['features']
        model = ActorCritic(len(f['global']), len(f['candidates'][0]), config['hidden'])
        result, optimizer = bc_update(model, samples, args.epochs, config['lr'], config['batch_size'])
        save_checkpoint(args.out, model, header['schema'], header['rules'], {'algorithm': 'BC', 'config': config,
                        'source': header, 'metrics': result}, optimizer)
        print(json.dumps({'algorithm': 'BC', **result, 'seconds': time.monotonic() - start}))
        return
    with Environment(args.env_command) as env:
        if args.command == 'demo':
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            with open(args.out, 'w') as stream:
                for i in range(args.episodes):
                    seed = args.seed + i
                    demo = env.request('demo', seed=seed, difficulty=args.difficulty)
                    terminal = demo['terminal']
                    if terminal['engineErrors'] or terminal['actionErrors'] or not terminal['done']:
                        raise RuntimeError('Invalid teacher episode')
                    if i == 0:
                        stream.write(json.dumps({'type': 'metadata', 'schema': demo['schema'], 'rules': demo['rules'],
                                                 'teacher': demo['teacher']}) + '\n')
                    for sample in demo['samples']:
                        stream.write(json.dumps({'type': 'sample', 'seed': seed, 'difficulty': args.difficulty,
                                                 'victory': terminal['victory'], **sample}) + '\n')
                    stream.write(json.dumps({'type': 'episode', 'seed': seed, 'difficulty': args.difficulty,
                                             'rejectedLabels': demo['rejectedLabels'], **episode_result(terminal)}) + '\n')
                    print(json.dumps({'seed': seed, 'samples': len(demo['samples']), **episode_result(terminal)}), flush=True)
            return
        model, ckpt = load_checkpoint(args.checkpoint)
        # Validate schema even in eval, before the first policy action.
        probe = env.request('reset', seed=args.seed, difficulty='NORMAL', budget=config['prep_budget'])
        validate_checkpoint(ckpt, probe)
        if args.command == 'eval':
            results = []
            for i in range(args.episodes):
                trace = None
                if args.trace_dir:
                    Path(args.trace_dir).mkdir(parents=True, exist_ok=True)
                    trace = Path(args.trace_dir) / f'{args.difficulty}-{args.seed+i}.json'
                r = evaluate(env, model, args.seed + i, args.difficulty, config['prep_budget'], trace)
                results.append(r)
                print(json.dumps(r), flush=True)
            print(json.dumps({'episodes': len(results), 'wins': sum(r['victory'] for r in results),
                              'winRate': sum(r['victory'] for r in results) / len(results)}))
            return
        if args.steps_per_stage < 1:
            raise ValueError('steps-per-stage must be positive')
        optimizer = torch.optim.Adam(model.parameters(), lr=config['lr'])
        total_steps, episode_seed = 0, args.seed
        for difficulty in args.curriculum.split(','):
            if difficulty not in ['NORMAL', 'HARD', 'ABYSS']:
                raise ValueError(f'Unknown curriculum difficulty {difficulty}')
            def reset():
                nonlocal episode_seed
                state = env.request('reset', seed=episode_seed, difficulty=difficulty, budget=config['prep_budget'])
                episode_seed += 1
                return state
            state = reset()
            stage_steps = 0
            while stage_steps < args.steps_per_stage:
                count = min(config['rollout_steps'], args.steps_per_stage - stage_steps)
                rollout, bootstrap, state, episodes = collect_rollout(env, model, state, count, reset)
                metrics = ppo_update(model, optimizer, rollout, bootstrap, config)
                stage_steps += count
                total_steps += count
                report = {'algorithm': 'masked-PPO', 'difficulty': difficulty, 'totalSteps': total_steps,
                          'stageSteps': stage_steps, 'episodes': episodes, **metrics}
                save_checkpoint(args.out, model, ckpt['schema'], ckpt['rules'],
                                {'config': config, 'metrics': report, 'parent': str(args.checkpoint),
                                 'nextSeed': episode_seed, 'incompleteEpisodeDiscardedOnRestart': not state['done']}, optimizer)
                print(json.dumps(report), flush=True)
            if not state['done']:
                print(json.dumps({'difficulty': difficulty, 'curriculumTruncation': True,
                                  'round': state['rounds'], 'victory': False, 'note': 'Not an episode result; final transition bootstrapped'}), flush=True)
        print(json.dumps({'seconds': time.monotonic() - start, 'totalSteps': total_steps}))


if __name__ == '__main__':
    main()
