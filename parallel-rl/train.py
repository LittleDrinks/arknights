#!/usr/bin/env python3
"""Synchronous CPU PPO: persistent spawn actors, one learner, shared ShopEnv protocol."""
import os

# Set before importing Torch, including in spawn children. Preserve all other env vars.
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
import argparse
import ctypes
import hashlib
import json
import math
import multiprocessing as mp
import queue
import threading
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import torch
from rl.learner import (ActorCritic, Environment, DEFAULT_ENV, gae, save_checkpoint,
                        load_checkpoint, validate_checkpoint, episode_result, vector, digest)

STRIDE = 4096
FROZEN = ((90001, 90016), (100001, 100064))
FIELDS = ('features', 'actions', 'logprobs', 'values', 'rewards', 'dones', 'ends', 'next_values')


def seed_ranges(base, workers, episodes):
    if workers < 1 or not 1 <= episodes <= STRIDE:
        raise ValueError('workers >= 1 and episodes-per-worker in 1..4096 required')
    ranges = []
    for worker in range(workers):
        first = base + worker * STRIDE
        last = first + episodes - 1
        if first < 1 or last > 0xffffffff or any(first <= hi and last >= lo for lo, hi in FROZEN):
            raise ValueError(f'Unsafe game seed range for worker {worker}: {first}..{last}')
        ranges.append({'worker': worker, 'first': first, 'last': last})
    return ranges


def pack(model):
    # Plain Python IPC avoids shared Torch storage/resource-sharer lifetime coupling.
    return {key: value.tolist() for key, value in model.state_dict().items()}


def stop_signal(signum, _frame):
    raise SystemExit(f'signal {signum}')


def actor(connection, worker, args, config, schema, rules, dimensions, parent_pid):
    env = None
    try:
        os.setsid()  # Actor and all its Node descendants have their own killable group.
        signal.signal(signal.SIGTERM, stop_signal)
        # Linux parent-death cleanup also covers an unexpectedly killed learner.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'PR_SET_PDEATHSIG')
        if os.getppid() != parent_pid:
            raise RuntimeError('Learner exited during actor startup')
        torch.manual_seed(args.seed + worker + 1)
        model = ActorCritic(*dimensions).eval().requires_grad_(False)
        env = Environment(args.env_command, timeout=args.timeout)
        episode = 0
        episode_return, episode_steps = 0.0, 0
        reference = {'schema': schema, 'rules': rules, 'dimensions': dimensions}

        def reset():
            nonlocal episode, episode_return, episode_steps
            if episode >= args.episodes_per_worker:
                raise RuntimeError(f'worker {worker} exhausted its reserved game seed range')
            seed = args.seed + worker * STRIDE + episode
            state = env.request('reset', seed=seed, mode=args.mode,
                                difficulty=args.difficulty, budget=config['prep_budget'])
            validate_checkpoint(reference, state)
            require_training_partners(state)
            if state['done']:
                raise RuntimeError('Reset returned a terminal observation')
            episode += 1
            episode_return, episode_steps = 0.0, 0
            return state

        state = reset()
        connection.send({'kind': 'ready', 'worker': worker, 'episode': state['episode'],
                         'nodePid': env.process.pid})
        previous_version = -1
        while True:
            request = connection.recv()
            if request['kind'] == 'close':
                break
            version, count = request['version'], request['count']
            if version != previous_version + 1 or count < 0:
                raise RuntimeError('Out-of-order policy version or invalid rollout size')
            model.load_state_dict({k: torch.tensor(v) for k, v in request['parameters'].items()})
            if digest(model) != request['policySHA256']:
                raise RuntimeError('Actor parameter digest differs from learner snapshot')
            previous_version = version
            trajectory = {key: [] for key in FIELDS}
            episodes, truncations, reward_modes = [], [], set()
            for _ in range(count):
                if state['done'] or state.get('truncated') or state.get('terminated'):
                    state = reset()
                features = state['features']
                action, logprob, value = model.act(features)
                state = env.request('step', candidateIndex=action)
                truncated = bool(state.get('truncated', False))
                terminal = bool(state.get('terminated', state['done'] and not truncated))
                end = terminal or truncated or bool(state['done'])
                reward = float(state['reward'])
                next_value = 0.0 if terminal else model.value(state['features'])
                if not all(math.isfinite(x) for x in (reward, value, logprob, next_value)):
                    raise RuntimeError('Non-finite environment transition')
                values = (features, action, logprob, value, reward, terminal, end, next_value)
                for key, item in zip(FIELDS, values):
                    trajectory[key].append(item)
                episode_return += reward
                episode_steps += 1
                fallback = 'terminal/v1' if args.env_command == DEFAULT_ENV else args.reward_mode
                reward_modes.add(state.get('trainingReward', {}).get('mode', fallback))
                if end:
                    result = {**episode_result(state), 'worker': worker,
                              'sampledSteps': episode_steps, 'trainingReturn': episode_return}
                    (episodes if terminal else truncations).append(result)
            connection.send({'kind': 'rollout', 'worker': worker, 'version': version,
                             'policySHA256': request['policySHA256'], 'trajectory': trajectory,
                             'episodes': episodes, 'truncations': truncations,
                             'rewardModes': sorted(reward_modes), 'episodesStarted': episode,
                             'unfinishedEpisodeSteps': 0 if state['done'] or state.get('truncated')
                             or state.get('terminated') else episode_steps})
    except SystemExit:
        pass  # Parent shutdown/parent death; finally still closes and reaps Node.
    except BaseException:
        try:
            connection.send({'kind': 'error', 'worker': worker, 'error': traceback.format_exc()})
        except (OSError, EOFError):
            pass
        raise
    finally:
        try:
            if env is not None:
                env.close()
        finally:
            connection.close()


def require_training_partners(state):
    partners = state.get('episode', {}).get('partners')
    if partners is not None:
        if partners.get('split') != 'train' or partners.get('pool') == 'heldout':
            raise ValueError('Evaluation/heldout partners cannot enter training')
        for member in partners.get('seats', []):
            if member.get('id') == 'random-v1' or member.get('family') == 'random-legal':
                raise ValueError('Heldout partner family cannot enter training')


def bounded_ipc(connections, processes, timeout, operation, expected):
    # Pipe readability can mean only a partial length header/payload. Perform the
    # entire blocking recv/send in daemon threads, so the controlling thread keeps
    # checking deadlines and can kill/reap actors even if an IPC frame stalls.
    completed = queue.Queue()
    def transfer(worker, connection):
        try: completed.put((worker, operation(worker, connection), None))
        except BaseException as error: completed.put((worker, None, error))
    for worker, connection in enumerate(connections):
        threading.Thread(target=transfer, args=(worker, connection), daemon=True).start()
    pending = dict(enumerate(connections))
    results = {}
    deadline = time.monotonic() + timeout
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'{expected} barrier timed out; pending workers {sorted(pending)}')
        try: worker, result, error = completed.get(timeout=min(remaining, 0.1))
        except queue.Empty: pass
        else:
            if error is not None: raise RuntimeError(f'Worker {worker} IPC failed during {expected}') from error
            results[worker] = result
            del pending[worker]
        # An already-replied actor dying also invalidates the whole arm.
        for worker, process in enumerate(processes):
            if not process.is_alive():
                raise RuntimeError(f'Worker {worker} exited with code {process.exitcode}')
    return [results[worker] for worker in sorted(results)]


def receive_all(connections, processes, timeout, expected):
    def receive(worker, connection):
        result = connection.recv()
        if result['kind'] != expected or result['worker'] != worker:
            raise RuntimeError(f'Worker {worker} failed: {result}')
        return result
    return bounded_ipc(connections, processes, timeout, receive, expected)


def stop_actors(connections, processes):
    # SIGTERM interrupts a blocked environment request; then reap direct children.
    for process in processes:
        if process.is_alive():
            process.terminate()
    deadline = time.monotonic() + 3
    for process in processes:
        process.join(max(0, deadline - time.monotonic()))
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # Also descendants of an exited actor.
        except ProcessLookupError:
            pass
        if process.is_alive():  # Includes a child interrupted before setsid().
            process.kill()
        process.join()
    for connection in connections:
        connection.close()


def merge_trajectories(results, config):
    merged = {key: [] for key in FIELDS}
    advantages, returns = [], []
    for result in sorted(results, key=lambda row: row['worker']):
        trajectory = result['trajectory']
        n = len(trajectory['actions'])
        if any(len(trajectory[key]) != n for key in FIELDS):
            raise ValueError('Malformed actor trajectory')
        # GAE resets at every episode boundary, including explicit time-limit resets.
        # A sampling boundary is NOT terminal: use the same frozen policy's V(s_next).
        start = 0
        for i in range(n):
            if trajectory['ends'][i] or i == n - 1:
                sl = slice(start, i + 1)
                adv, ret = gae(trajectory['rewards'][sl], trajectory['dones'][sl],
                               trajectory['values'][sl], trajectory['next_values'][i],
                               config['gamma'], config['gae_lambda'])
                advantages.append(adv)
                returns.append(ret)
                start = i + 1
        for key in FIELDS:
            merged[key].extend(trajectory[key])
    if not advantages:
        raise ValueError('Empty global rollout')
    return merged, torch.cat(advantages), torch.cat(returns)


def update(model, optimizer, results, config):
    rollout, advantage, returns = merge_trajectories(results, config)
    if len(advantage) > 1:
        advantage = (advantage - advantage.mean()) / (advantage.std(unbiased=False) + 1e-8)
    old = torch.tensor(rollout['logprobs'])  # Immutable behavior probabilities from actors.
    actions = torch.tensor(rollout['actions'])
    before = vector(model)
    # Validate synchronization BEFORE any optimizer step; never replace old logprobs.
    max_error = 0.0
    singleton_error = None
    fallback_used = False
    with torch.no_grad():
        for start in range(0, len(actions), config['batch_size']):
            sl = slice(start, start + config['batch_size'])
            distribution, _ = model(rollout['features'][sl])
            error = (distribution.log_prob(actions[sl]) - old[sl]).abs().max().item()
            # Reject each non-finite batch before max() can hide a previous NaN.
            if not math.isfinite(error):
                raise RuntimeError(f'Behavior policy mismatch: non-finite batch logprob error {error}')
            max_error = max(error, max_error)
        accepted_error = max_error
        if max_error > 2e-5:
            # FP32 batched kernels can differ from actor.act's singleton forward.
            # Check EVERY state via that original path, without sampling an action
            # or replacing the immutable PPO denominator with recomputed logprobs.
            fallback_used = True
            singleton_error = 0.0
            for i, features in enumerate(rollout['features']):
                distribution, _ = model([features])
                error = (distribution.log_prob(actions[i:i + 1]) - old[i:i + 1]).abs().item()
                if not math.isfinite(error):
                    raise RuntimeError(f'Behavior policy mismatch: batch max logprob error {max_error}, '
                                       f'non-finite singleton logprob error {error} at state {i}')
                singleton_error = max(error, singleton_error)
            if singleton_error > 2e-5:
                raise RuntimeError(f'Behavior policy mismatch: batch max logprob error {max_error}, '
                                   f'singleton max logprob error {singleton_error}')
            accepted_error = singleton_error
    updates = 0
    for _ in range(config['epochs']):
        for indices in torch.randperm(len(actions)).split(config['batch_size']):
            distribution, value = model([rollout['features'][i] for i in indices.tolist()])
            logprob = distribution.log_prob(actions[indices])
            ratio = (logprob - old[indices]).exp()
            clipped = ratio.clamp(1 - config['clip'], 1 + config['clip'])
            policy_loss = -torch.minimum(ratio * advantage[indices], clipped * advantage[indices]).mean()
            value_loss = (value - returns[indices]).square().mean()
            entropy = distribution.entropy().mean()
            loss = policy_loss + config['value_coef'] * value_loss - config['entropy_coef'] * entropy
            if not torch.isfinite(loss):
                raise RuntimeError('Non-finite PPO loss')
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), config['max_grad_norm'], error_if_nonfinite=True)
            optimizer.step()
            updates += 1
    return {'loss': loss.item(), 'policyLoss': policy_loss.item(), 'valueLoss': value_loss.item(),
            'entropy': entropy.item(), 'optimizerSteps': updates, 'environmentSteps': len(actions),
            'terminalTransitions': sum(rollout['dones']), 'behaviorLogprobMaxError': max_error,
            'behaviorLogprobSingletonMaxError': singleton_error,
            'behaviorLogprobFallbackUsed': fallback_used,
            'behaviorLogprobAcceptedMaxError': accepted_error,
            'parameterDeltaL2': (vector(model) - before).norm().item(), 'parameterSHA256': digest(model)}


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_checkpoint(path, model, checkpoint, metadata, optimizer):
    temporary = path.with_name(path.name + '.tmp')
    save_checkpoint(temporary, model, checkpoint['schema'], checkpoint['rules'], metadata, optimizer)
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def sha(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def provenance(args, config, checkpoint):
    files = set()
    directories = [ROOT / name for name in ('rl', 'reward', 'parallel-rl')]
    for argument in shlex.split(args.env_command):
        path = Path(argument).resolve()
        if path.is_file():
            files.add(path)
            if path.suffix in ('.mjs', '.py', '.js'):
                directories.extend((path.parent, path.parent.parent / 'rl'))
    if os.environ.get('ARK_ROOT'):
        directories.append(Path(os.environ['ARK_ROOT']) / 'experiment-code/rl')
    for directory in directories:
        for path in directory.glob('*'):
            if path.is_file() and path.suffix in ('.py', '.mjs', '.js', '.json'):
                files.add(path.resolve())
    for name in args.provenance_file:
        files.add(Path(name).resolve())
    env_names = ('STRONGHOLD_GAME', 'ARK_ROOT', 'ARK_GAME_ROOT', 'COOP_POOL', 'COOP_SPLIT',
                 'COOP_POOL_SEED', 'COOP_TEAM', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS')
    return {'algorithm': 'synchronous-masked-PPO', 'baseline': '0de8ae5cc1b876dcd5c929231208c78ffe180cef',
            'sourceHEAD': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
            'sourceSHA256': {str(path): sha(path) for path in sorted(files)},
            'checkpointInput': {'path': str(Path(args.checkpoint).resolve()), 'sha256': sha(args.checkpoint)},
            'configInput': {'path': str(Path(args.config).resolve()), 'sha256': sha(args.config)},
            'schema': checkpoint['schema'], 'rules': checkpoint['rules'], 'hyperparameters': config,
            'arguments': vars(args), 'poolEnvironment': {key: os.environ.get(key) for key in env_names},
            'rewardMode': args.reward_mode, 'observedRewardModes': [],
            'seedRanges': seed_ranges(args.seed, args.workers, args.episodes_per_worker),
            'frozenSeedRanges': FROZEN, 'torchSeeds': [args.seed + w + 1 for w in range(args.workers)],
            'learnerTorchSeed': args.seed, 'torchVersion': str(torch.__version__),
            'restart': 'Warm start only; optimizer/RNG/environment continuation is not restored.'}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(ROOT / 'rl/config.json'))
    parser.add_argument('--checkpoint', required=True, help='Validated BC/PPO input checkpoint')
    parser.add_argument('--out', required=True, help='Output checkpoint; sidecar .manifest.json and .jsonl')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--steps', type=int, default=12000, help='Exact total transitions across all workers')
    parser.add_argument('--rollout', type=int, help='Transitions per worker per update (config rollout_steps)')
    parser.add_argument('--seed', type=int, help='Base game/learner seed (config seed, otherwise 1)')
    parser.add_argument('--episodes-per-worker', type=int, default=STRIDE)
    parser.add_argument('--env-command', default=DEFAULT_ENV)
    parser.add_argument('--mode', choices=('solo', 'coop'), default='solo')
    parser.add_argument('--difficulty', choices=('NORMAL', 'HARD', 'ABYSS'), default='NORMAL')
    parser.add_argument('--reward-mode', default='env-defined', help='Manifest label; observed modes also recorded')
    parser.add_argument('--provenance-file', action='append', default=[], help='Extra wrapper/pool/config source to hash')
    parser.add_argument('--timeout', type=float, default=120, help='Per environment request deadline, seconds')
    parser.add_argument('--batch-timeout', type=float, default=600, help='Whole actor barrier deadline, seconds')
    args = parser.parse_args()
    if os.environ.get('COOP_SPLIT', 'train') != 'train' or os.environ.get('COOP_POOL') == 'heldout':
        parser.error('PPO training refuses evaluation splits and heldout partner pools')
    config = json.loads(Path(args.config).read_text())
    args.seed = args.seed if args.seed is not None else config.get('seed', 1)
    args.rollout = args.rollout if args.rollout is not None else config['rollout_steps']
    config['rollout_steps'] = args.rollout
    for name in ('seed', 'steps', 'rollout'):
        if type(getattr(args, name)) is not int or getattr(args, name) < 1:
            parser.error(f'{name} must be a positive integer')
    seed_ranges(args.seed, args.workers, args.episodes_per_worker)
    for name in ('timeout', 'batch_timeout'):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f'{name} must be positive and finite')
    for name in ('epochs', 'batch_size', 'prep_budget'):
        if type(config[name]) is not int or config[name] < 1:
            parser.error(f'{name} must be a positive integer')
    for name in ('lr', 'gamma', 'gae_lambda', 'clip', 'value_coef', 'entropy_coef', 'max_grad_norm'):
        if not math.isfinite(config[name]) or config[name] < 0:
            parser.error(f'{name} must be nonnegative and finite')
    if config['lr'] == 0 or config['max_grad_norm'] == 0 or not 0 < config['clip'] < 1:
        parser.error('lr/max_grad_norm must be positive; clip must be in (0,1)')
    if config['gamma'] > 1 or config['gae_lambda'] > 1 or config['prep_budget'] > 1024:
        parser.error('gamma/lambda <= 1 and prep_budget <= 1024 required')
    if Path(args.out).resolve() == Path(args.checkpoint).resolve():
        parser.error('Input checkpoint must not be overwritten')
    return args, config


def main():
    args, config = parse_args()
    signal.signal(signal.SIGTERM, stop_signal)
    torch.manual_seed(args.seed)
    model, checkpoint = load_checkpoint(args.checkpoint)
    config['hidden'] = model.dimensions[2]
    optimizer = torch.optim.Adam(model.parameters(), lr=config['lr'])
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output.with_name(output.name + '.manifest.json')
    log_path = output.with_name(output.name + '.jsonl')
    if any(path.exists() for path in (output, manifest_path, log_path)):
        raise FileExistsError('Choose a fresh --out; existing runs are not overwritten')
    manifest = provenance(args, config, checkpoint)
    manifest.update(status='starting', totalSteps=0, completedEpisodes=0)
    atomic_json(manifest_path, manifest)
    connections, processes = [], []
    start = time.monotonic()
    try:
        context = mp.get_context('spawn')
        for worker in range(args.workers):
            parent, child = context.Pipe()
            process = context.Process(target=actor, args=(child, worker, args, config,
                                      checkpoint['schema'], checkpoint['rules'], model.dimensions, os.getpid()))
            process.start()
            child.close()
            connections.append(parent)
            processes.append(process)
        manifest['actors'] = receive_all(connections, processes, args.batch_timeout, 'ready')
        manifest['actorPids'] = [process.pid for process in processes]
        manifest['status'] = 'running'
        atomic_json(manifest_path, manifest)
        total, version, completed = 0, 0, 0
        with log_path.open('w') as log:
            while total < args.steps:
                count = min(args.steps - total, args.workers * args.rollout)
                quotient, remainder = divmod(count, args.workers)
                counts = [quotient + (worker < remainder) for worker in range(args.workers)]
                parameters, policy_sha = pack(model), digest(model)
                def dispatch(worker, connection):
                    connection.send({'kind': 'sample', 'version': version, 'count': counts[worker],
                                     'parameters': parameters, 'policySHA256': policy_sha})
                bounded_ipc(connections, processes, args.batch_timeout, dispatch, 'parameter send')
                results = receive_all(connections, processes, args.batch_timeout, 'rollout')
                for worker, result in enumerate(results):
                    if (result['version'] != version or result['policySHA256'] != policy_sha
                            or len(result['trajectory']['actions']) != counts[worker]):
                        raise RuntimeError('Actor version/digest/step count mismatch')
                metrics = update(model, optimizer, results, config)
                total += metrics['environmentSteps']
                episodes = [episode for result in results for episode in result['episodes']]
                completed += len(episodes)
                observed = {mode for result in results for mode in result['rewardModes']}
                manifest['observedRewardModes'] = sorted(set(manifest['observedRewardModes']) | observed)
                report = {'update': version + 1, 'behaviorVersion': version, 'behaviorSHA256': policy_sha,
                          'totalSteps': total, 'workerSteps': counts, 'completedEpisodes': len(episodes),
                          'totalCompletedEpisodes': completed, 'episodes': episodes,
                          'truncations': [item for result in results for item in result['truncations']],
                          'episodesStarted': [result['episodesStarted'] for result in results],
                          'unfinishedEpisodeSteps': [result['unfinishedEpisodeSteps'] for result in results],
                          **metrics}
                manifest.update(totalSteps=total, completedEpisodes=completed, lastUpdate=version + 1)
                snapshot_manifest = {k: v for k, v in manifest.items() if k != 'outputSHA256'}
                atomic_checkpoint(output, model, checkpoint, {'manifest': snapshot_manifest, 'report': report}, optimizer)
                manifest['outputSHA256'] = sha(output)
                atomic_json(manifest_path, manifest)
                log.write(json.dumps(report, allow_nan=False) + '\n')
                log.flush()
                print(json.dumps(report, allow_nan=False), flush=True)
                version += 1
        manifest['status'] = 'complete'
    except BaseException:
        manifest.update(status='failed', error=traceback.format_exc())
        raise
    finally:
        stop_actors(connections, processes)
        manifest['seconds'] = time.monotonic() - start
        atomic_json(manifest_path, manifest)


if __name__ == '__main__':
    main()
