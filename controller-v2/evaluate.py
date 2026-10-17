#!/usr/bin/env python3
"""Two paired CPU episodes, same immutable BC, no training; hard process-tree budgets.
Run: python3 -B controller-v2/evaluate.py
Writes only controller-v2/results/. Linux /proc supplies RSS and process-tree cleanup.
"""
import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LIMIT_SECONDS = 40  # leaves five seconds of the requested sim budget for fixtures/cleanup
LIMIT_RSS = 768 * 1024 * 1024
LIMIT_BYTES = 20 * 1024 * 1024


def worker(args):
    # Import unchanged learner utilities; prevent __pycache__ outside the allowed directory.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / 'rl'))
    from learner import Environment, load_checkpoint, validate_checkpoint, episode_result
    checkpoint = Path(args.checkpoint).resolve()
    model, ckpt = load_checkpoint(checkpoint)
    model.eval()
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    out = HERE / 'results'
    out.mkdir(exist_ok=True)
    command = f'node --max-old-space-size=256 --loader {HERE / "loader.mjs"} {ROOT / "rl/env.mjs"}'
    reports = []
    for variant in ['off', 'v2']:
        os.environ['ARK_ITEM_CONTROLLER'] = variant
        state, actions = None, []
        start = time.monotonic()
        result = {'variant': variant, 'checkpointSHA256': digest, 'seed': args.seed,
                  'difficulty': args.difficulty, 'status': 'started', 'device': 'cpu'}
        try:
            with Environment(command, timeout=LIMIT_SECONDS) as env:
                state = env.request('reset', seed=args.seed, difficulty=args.difficulty, budget=128)
                validate_checkpoint(ckpt, state)
                while not state['done']:
                    action, _, _ = model.act(state['features'], greedy=True)
                    actions.append({'round': state['rounds'], 'candidateIndex': action, 'action': state['candidates'][action]})
                    state = env.request('step', candidateIndex=action)
                result.update(episode_result(state), status='complete', controller=state['controller'], rules=state['rules'])
                illicit = [a for a in state['observation']['history']['actions']
                           if a['type'] in ['buy', 'sell', 'refresh', 'levelUp'] and a['source'] != 'policy']
                result['nonPolicyEconomyActions'] = len(illicit)
                if illicit:
                    raise AssertionError('Controller made shop economy decisions')
                if state['actionErrors'] or state['engineErrors']:
                    raise AssertionError('Invalid engine trajectory')
        except Exception as exc:
            result.update(status='stopped', error=str(exc)[:4000])
        result['seconds'] = time.monotonic() - start
        result['actionHash'] = hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest()
        history = state['observation']['history'] if state else {}
        result['itemAuditCounts'] = history.get('itemController', {}).get('counts', {})
        report = {'result': result, 'actions': actions, 'history': history,
                  'summary': state.get('summary') if state else None,
                  'stateIsTerminal': bool(state and state['done']),
                  'note': 'Natural shop-policy rollout. On stop, history is the last successful response.'}
        (out / f'{variant}.json').write_text(json.dumps(report, ensure_ascii=False) + '\n')
        reports.append(result)
        print(json.dumps(result), flush=True)
    (out / 'summary.json').write_text(json.dumps(reports, indent=2) + '\n')
    return 0 if all(r['status'] == 'complete' for r in reports) else 1


def tree(root):
    found, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid in found:
            continue
        found.append(pid)
        try:
            todo.extend(map(int, Path(f'/proc/{pid}/task/{pid}/children').read_text().split()))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0


def supervise(args):
    start, peak, reason = time.monotonic(), 0, None
    child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--worker',
                              '--checkpoint', args.checkpoint, '--seed', str(args.seed), '--difficulty', args.difficulty],
                             start_new_session=True, cwd=ROOT)
    try:
        while child.poll() is None:
            peak = max(peak, sum(rss(p) for p in tree(child.pid)) + rss(os.getpid()))
            size = sum(p.stat().st_size for p in HERE.rglob('*') if p.is_file())
            if time.monotonic() - start > LIMIT_SECONDS:
                reason = 'wall-time-budget'
            elif peak >= LIMIT_RSS:
                reason = 'aggregate-RSS-budget'
            elif size >= LIMIT_BYTES - 65536:
                reason = 'artifact-budget'
            if reason:
                os.killpg(child.pid, signal.SIGKILL)
                break
            time.sleep(0.05)
        code = child.wait()
    finally:
        # Includes a Node child if Python exits abnormally.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    evidence = {'seconds': time.monotonic() - start, 'peakAggregateRSSBytes': peak,
                'exitCode': code, 'stopReason': reason, 'limits': {'seconds': LIMIT_SECONDS,
                'rssBytes': LIMIT_RSS, 'artifactBytes': LIMIT_BYTES}, 'rssSampleSeconds': 0.05}
    (HERE / 'results').mkdir(exist_ok=True)
    (HERE / 'results' / 'resources.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence), flush=True)
    return code if code >= 0 else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', default=str(ROOT.parent / 'experiment-rl/rl/artifacts/bc.pt'))
    p.add_argument('--seed', type=int, default=60001)
    p.add_argument('--difficulty', choices=['NORMAL', 'HARD', 'ABYSS'], default='NORMAL')
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = p.parse_args()
    raise SystemExit(worker(args) if args.worker else supervise(args))
