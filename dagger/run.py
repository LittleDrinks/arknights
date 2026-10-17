#!/usr/bin/env python3
"""Current-state DAgger collection and warm-start BC; all artifacts must live in /tmp."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import random
import shlex
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'rl'))
import learner as L

CONTROLLER = 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1'
ENV = f'node --max-old-space-size=384 {shlex.quote(str(ROOT / "dagger/env.mjs"))}'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def source_hashes():
    return {str(p.relative_to(ROOT)): sha(p) for pattern in ['dagger/*.py', 'dagger/*.mjs', 'rl/*.mjs', 'rl/learner.py']
            for p in sorted(ROOT.glob(pattern))}


def artifact(path):
    p = Path(path).resolve()
    if not p.is_relative_to('/tmp'):
        raise ValueError('Artifacts must be under /tmp')
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() or Path(str(p) + '.manifest.json').exists():
        raise ValueError(f'Refusing to overwrite artifact: {p}')
    return p


def stream(path, mode):
    return gzip.open(path, mode + 't') if str(path).endswith('.gz') else open(path, mode)


def write(f, row):
    f.write(json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n')


def manifest(path, report):
    report['artifact'] = {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}
    Path(str(path) + '.manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


def collect(args):
    out = artifact(args.out)
    start = time.monotonic()
    model, ckpt = L.load_checkpoint(args.checkpoint)
    model.eval()
    # Loading initializes a module: explicitly start sampling RNG after loading.
    L.torch.manual_seed(args.seed)
    mixing = random.Random(args.seed)
    report = {'algorithm': 'DAgger-current-state', 'teacherKind': 'weak-heuristic',
              'parent': {'path': str(Path(args.checkpoint).resolve()), 'sha256': sha(args.checkpoint)},
              'betaSchedule': args.beta, 'betaRule': 'per episode, repeat last value',
              'seed': args.seed, 'episodesRequested': args.episodes, 'difficulty': args.difficulty,
              'budget': args.budget, 'safety': 'off', 'sampling': 'model.act(greedy=False)',
              'controller': CONTROLLER, 'sources': source_hashes(), 'episodes': []}
    with stream(out, 'x') as f:
        try:
            with L.Environment(ENV, timeout=20) as env:
                for ep in range(args.episodes):
                    seed = args.seed + ep
                    beta = args.beta[min(ep, len(args.beta) - 1)]
                    state = env.request('reset', seed=seed, difficulty=args.difficulty, budget=args.budget)
                    L.validate_checkpoint(ckpt, state)
                    if state['controller'] != CONTROLLER:
                        raise ValueError('Fixed controller version mismatch')
                    if ep == 0:
                        report.update({k: state[k] for k in ['rules', 'schema', 'teacherVersion', 'teacherHash']})
                        write(f, {'type': 'metadata', **report})
                    model_steps = teacher_steps = 0
                    while not state['done']:
                        teacher = state['teacherIndex']
                        mask = state['features']['mask']
                        if type(teacher) is not int or not 0 <= teacher < len(mask) or not mask[teacher]:
                            raise ValueError('Illegal current-state teacher label')
                        if any(state[k] != report[k] for k in ['teacherHash', 'teacherVersion', 'controller']):
                            raise ValueError('Policy identity changed during collection')
                        proposed, logprob, value = model.act(state['features'])
                        use_teacher = mixing.random() < beta
                        action = teacher if use_teacher else proposed
                        teacher_steps += int(use_teacher)
                        model_steps += int(not use_teacher)
                        write(f, {'type': 'sample', 'source': 'dagger-current-state', 'episode': ep,
                                  'seed': seed, 'difficulty': args.difficulty, 'round': state['rounds'],
                                  'step': state['steps'], 'features': state['features'],
                                  'candidates': state['candidates'], 'action': teacher,
                                  'teacherIndex': teacher, 'teacherVersion': state['teacherVersion'],
                                  'teacherHash': state['teacherHash'], 'teacherScores': state['teacherScores'],
                                  'beta': beta, 'modelIndex': proposed, 'modelLogprob': logprob, 'modelValue': value,
                                  'executedIndex': action, 'behaviorSource': 'teacher' if use_teacher else 'model'})
                        state = env.request('step', candidateIndex=action)
                    # Full original terminal response, including summary, win and all error counters/history.
                    write(f, {'type': 'episode', 'episode': ep, 'seed': seed, 'difficulty': args.difficulty,
                              'beta': beta, 'terminal': state})
                    if state['controllerErrors']:
                        raise RuntimeError('Controller error: terminal retained, run invalid for training')
                    report['episodes'].append({'episode': ep, **L.episode_result(state), 'done': state['done'],
                                               'summary': state['summary'], 'beta': beta,
                                               'modelSteps': model_steps, 'teacherSteps': teacher_steps})
        except Exception as e:
            report['error'] = str(e)
            write(f, {'type': 'error', 'error': str(e), 'detail': e.args[0] if e.args else None,
                      'episode': locals().get('ep'), 'seed': locals().get('seed'),
                      'difficulty': args.difficulty, 'terminal': None, 'done': False})
            raise
        finally:
            f.flush()
            # Finish gzip trailer before hashing, including failed runs.
            f.close()
            report['seconds'] = time.monotonic() - start
            manifest(out, report)


def load_samples(path, ckpt, limit, existing=False):
    samples, header, completed = [], None, set()
    with stream(path, 'r') as f:
        for line in f:
            row = json.loads(line)
            if row['type'] == 'metadata':
                if header is not None:
                    raise ValueError('Multiple metadata rows')
                header = row
                if header['schema'] != ckpt['schema'] or header['rules'] != ckpt['rules']:
                    raise ValueError('Dataset/checkpoint schema or rules mismatch')
                if not existing and (header.get('algorithm') != 'DAgger-current-state'
                                     or header.get('controller') != CONTROLLER or header.get('safety') != 'off'):
                    raise ValueError('Unexpected DAgger collection configuration')
            elif row['type'] == 'error':
                raise ValueError(f'Failed collection must not train: {path}')
            elif row['type'] == 'episode':
                terminal = row if existing else row['terminal']
                if any(terminal.get(k, 0) for k in ['engineErrors', 'actionErrors', 'controllerErrors']):
                    raise ValueError('Error episode must not train')
                if not existing and (not terminal['done'] or terminal['summary'] is None
                                     or terminal['victory'] != bool(terminal['summary']['victory'])):
                    raise ValueError('Incomplete/inconsistent terminal')
                completed.add((row['seed'], row['difficulty']))
            elif row['type'] == 'sample' and len(samples) < limit:
                if not header:
                    raise ValueError('Metadata must precede samples')
                L.validate_checkpoint(ckpt, {'schema': header['schema'], 'rules': header['rules'], 'features': row['features']})
                if not existing and (row['action'] != row['teacherIndex'] or row['teacherHash'] != header['teacherHash']
                                     or row['teacherVersion'] != header['teacherVersion']):
                    raise ValueError('Teacher provenance mismatch')
                samples.append({**row, 'source': 'existing-BC' if existing else 'dagger-current-state',
                                'sourceFile': str(Path(path).resolve())})
    if not header or not samples or any((s['seed'], s['difficulty']) not in completed for s in samples):
        raise ValueError('Missing metadata, samples, or complete episode record')
    return samples, {'path': str(Path(path).resolve()), 'sha256': sha(path),
                     'source': 'existing-BC' if existing else 'dagger-current-state',
                     'samples': len(samples), 'metadata': header}


def train(args):
    out = artifact(args.out)
    start = time.monotonic()
    model, ckpt = L.load_checkpoint(args.checkpoint)
    L.torch.manual_seed(args.seed)
    samples, sources = [], []
    for existing, paths in [(False, args.data), (True, args.existing_bc)]:
        for path in paths:
            rows, source = load_samples(path, ckpt, args.max_samples, existing)
            samples.extend(rows)
            sources.append(source)
    before = L.digest(model)
    metrics, optimizer = L.bc_update(model, samples, args.epochs, args.lr, args.batch_size)
    report = {'algorithm': 'DAgger-BC', 'seed': args.seed, 'teacherKind': 'weak-heuristic', 'safety': 'off',
              'parent': {'path': str(Path(args.checkpoint).resolve()), 'sha256': sha(args.checkpoint)},
              'beforeParameterSHA256': before, 'data': sources, 'sources': source_hashes(),
              'controller': CONTROLLER, 'config': {'epochs': args.epochs, 'lr': args.lr,
              'batchSize': args.batch_size, 'maxSamplesPerFile': args.max_samples}, 'metrics': metrics}
    L.save_checkpoint(out, model, ckpt['schema'], ckpt['rules'], report, optimizer)
    loaded, saved = L.load_checkpoint(out)
    assert L.digest(loaded) == L.digest(model)
    L.validate_checkpoint(saved, {'schema': ckpt['schema'], 'rules': ckpt['rules'], 'features': samples[0]['features']})
    assert loaded.act(samples[0]['features'], greedy=True)[0] == model.act(samples[0]['features'], greedy=True)[0]
    report['checkpointReloadExact'] = True
    report['seconds'] = time.monotonic() - start
    manifest(out, report)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('collect')
    t = sub.add_parser('train')
    for q in [c, t]:
        q.add_argument('--checkpoint', required=True)
        q.add_argument('--out', required=True)
        q.add_argument('--seed', type=int, default=101)
    c.add_argument('--episodes', type=int, default=1)
    c.add_argument('--difficulty', choices=['NORMAL', 'HARD', 'ABYSS'], default='NORMAL')
    c.add_argument('--budget', type=int, default=128)
    c.add_argument('--beta', default='0', help='Comma-separated per-episode mixing schedule; last repeats')
    t.add_argument('--data', action='append', required=True)
    t.add_argument('--existing-bc', action='append', default=[])
    t.add_argument('--epochs', type=int, default=1)
    t.add_argument('--max-samples', type=int, default=32, help='Per input file, in file order')
    t.add_argument('--batch-size', type=int, default=8)
    t.add_argument('--lr', type=float, default=3e-4)
    args = p.parse_args()
    if not 1 <= args.seed <= 0xffffffff:
        p.error('seed must be in 1..4294967295')
    if args.command == 'collect':
        try:
            args.beta = [float(x) for x in args.beta.split(',')]
        except ValueError:
            p.error('beta must be comma-separated probabilities')
        if not args.beta or any(not 0 <= x <= 1 for x in args.beta):
            p.error('every beta must be in [0,1]')
        if args.episodes < 1 or args.seed + args.episodes - 1 > 0xffffffff or not 1 <= args.budget <= 1024:
            p.error('invalid episodes/uint32 seed range/budget')
        collect(args)
    else:
        if min(args.epochs, args.max_samples, args.batch_size) < 1 or not 0 < args.lr < 1:
            p.error('positive epochs/samples/batch and finite lr in (0,1) required')
        train(args)


if __name__ == '__main__':
    main()
