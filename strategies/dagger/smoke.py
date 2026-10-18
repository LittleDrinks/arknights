#!/usr/bin/env python3
"""One complete autonomous rollout + four BC batches, bounded process-tree audit."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def rss_tree(root):
    total, stack = 0, [root]
    while stack:
        pid = stack.pop()
        try:
            total += int(Path(f'/proc/{pid}/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
            stack.extend(map(int, Path(f'/proc/{pid}/task/{pid}/children').read_text().split()))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return total


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--out-dir')
    args = p.parse_args()
    folder = Path(args.out_dir or tempfile.mkdtemp(prefix='dagger-smoke-')).resolve()
    if not folder.is_relative_to('/tmp'):
        p.error('out-dir must be under /tmp')
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        p.error('out-dir must be empty')
    begin, peak = time.monotonic(), 0
    commands = [
        ['node', '--test', 'dagger/contracts.test.mjs'],
        [sys.executable, '-B', 'dagger/run.py', 'collect', '--checkpoint', str(Path(args.checkpoint).resolve()),
         '--seed', '101', '--difficulty', 'NORMAL', '--beta', '0', '--budget', '128',
         '--out', str(folder / 'rollout.jsonl.gz')],
        [sys.executable, '-B', 'dagger/run.py', 'train', '--checkpoint', str(Path(args.checkpoint).resolve()),
         '--seed', '102', '--data', str(folder / 'rollout.jsonl.gz'), '--max-samples', '32',
         '--batch-size', '8', '--epochs', '1', '--out', str(folder / 'dagger.pt')],
    ]
    measurements = []
    for i, command in enumerate(commands):
        start = time.monotonic()
        with (folder / f'{i}.log').open('x') as log:
            child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                     env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'})
            try:
                while child.poll() is None:
                    peak = max(peak, rss_tree(os.getpid()))
                    size = sum(f.stat().st_size for f in folder.iterdir() if f.is_file())
                    if time.monotonic() - begin > 24 or peak > 1.1 * 1024**3 or size > 20_000_000:
                        raise RuntimeError('Smoke budget exceeded (24s guard / 1.1GiB / 20MB)')
                    time.sleep(0.025)
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        measurements.append({'command': command, 'seconds': time.monotonic() - start, 'exitCode': child.returncode})
        if child.returncode:
            raise RuntimeError(f'Command failed: inspect {folder / f"{i}.log"}')
    collect = json.loads((folder / 'rollout.jsonl.gz.manifest.json').read_text())
    train = json.loads((folder / 'dagger.pt.manifest.json').read_text())
    with gzip.open(folder / 'rollout.jsonl.gz', 'rt') as f:
        rows = [json.loads(line) for line in f]
    samples = [r for r in rows if r['type'] == 'sample']
    terminals = [r for r in rows if r['type'] == 'episode']
    assert len(terminals) == 1 and terminals[0]['terminal']['done']
    terminal = terminals[0]['terminal']
    assert all(s['behaviorSource'] == 'model' and s['executedIndex'] == s['modelIndex'] for s in samples)
    assert all(s['features']['mask'][s['teacherIndex']] and s['action'] == s['teacherIndex'] for s in samples)
    assert len(samples) == terminal['steps']
    assert terminal['victory'] == bool(terminal['summary']['victory'])
    assert terminal['engineErrors'] == terminal['actionErrors'] == terminal['controllerErrors'] == 0
    assert not [a for a in terminal['observation']['history']['actions']
                if a['type'] in ['buy', 'sell', 'refresh', 'levelUp'] and a['source'] != 'policy']
    assert train['metrics']['parameterDeltaL2'] > 0 and train['checkpointReloadExact']
    report = {'seconds': time.monotonic() - begin, 'peakProcessTreeRSSBytes': peak,
              'rssMethod': 'sum /proc statm resident of harness and descendants, sampled every 25ms; shared pages counted per process',
              'artifactDirectory': str(folder), 'commands': measurements, 'collection': collect,
              'training': train, 'samples': len(samples),
              'teacherModelDisagreements': sum(s['teacherIndex'] != s['modelIndex'] for s in samples),
              'daggerDirectoryBytes': sum(f.stat().st_size for f in (ROOT / 'dagger').rglob('*') if f.is_file()),
              'artifactBytesBeforeEvidence': sum(f.stat().st_size for f in folder.iterdir() if f.is_file()),
              'artifacts': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in folder.iterdir() if f.is_file()},
              'limitations': 'NORMAL one episode only; no ABYSS win, no post-update win-rate or improvement claim'}
    text = json.dumps(report, indent=2) + '\n'
    (folder / 'evidence.json').write_text(text)
    print(json.dumps({'evidence': str(folder / 'evidence.json'), 'seconds': report['seconds'], 'rssBytes': peak,
                      'samples': len(samples), 'victory': terminal['victory'], 'rounds': terminal['rounds'],
                      'metrics': train['metrics']}))


if __name__ == '__main__':
    main()
