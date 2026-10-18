#!/usr/bin/env python3
"""Linux bounded validation: whole process group <=20s, sampled aggregate RSS <768MiB, files <=20MB."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def group_rss(pgid):
    total = 0
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if os.getpgid(int(proc.name)) == pgid:
                for line in (proc / 'status').read_text().splitlines():
                    if line.startswith('VmRSS:'):
                        total += int(line.split()[1]) * 1024
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            pass
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--only', choices=['engine', 'learner', 'rejections', 'preflight'])
    parser.add_argument('--pattern', help='Node test name pattern (skip unrelated simulation)')
    args = parser.parse_args()
    if not 0 < args.seconds <= 20:
        parser.error('seconds must be in (0,20]')
    out = Path(tempfile.mkdtemp(prefix='coop-check-'))
    env = {**os.environ, 'COOP_CHECK_OUT': str(out), 'PYTHONDONTWRITEBYTECODE': '1',
           'COOP_POOL': 'mixed', 'COOP_SPLIT': 'train', 'COOP_POOL_SEED': '1',
           'COOP_TEAM': 'original-v1,buy2-v1,saver-v1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
           'NODE_OPTIONS': '--max-old-space-size=192', 'MALLOC_ARENA_MAX': '2'}
    commands = []
    if args.only == 'preflight': commands.append(['node', '--test', 'coop/preflight.test.mjs'])
    elif args.only == 'rejections': commands.append(['node', '--test', 'coop/rejections.test.mjs'])
    else:
        if args.only != 'learner':
            commands.append(['node', '--test', '--test-concurrency=1', 'coop/env.test.mjs', 'coop/preflight.test.mjs',
                             *([] if args.only else ['coop/rejections.test.mjs'])])
        if args.only != 'engine': commands.append([sys.executable, '-B', 'coop/smoke.py'])
    if args.pattern:
        if any(cmd[0] != 'node' for cmd in commands): parser.error('--pattern requires Node-only checks')
        for cmd in commands: cmd.insert(2, '--test-name-pattern=' + args.pattern)
    start, peak, size, failure = time.monotonic(), 0, 0, None
    try:
        for i, cmd in enumerate(commands):
            log = out / f'check-{i}.log'
            with log.open('wb') as stream:
                p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=stream, stderr=stream, start_new_session=True)
                try:
                    while True:
                        peak = max(peak, group_rss(p.pid))
                        size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
                        if time.monotonic() - start > args.seconds: raise RuntimeError('wall/simulation budget exceeded')
                        if peak > 700 * 1024 ** 2: raise RuntimeError('RSS safety margin exceeded (768 MiB ceiling)')
                        if size > 20_000_000: raise RuntimeError('artifact budget exceeded')
                        if p.poll() is not None: break
                        time.sleep(0.05)
                    if p.returncode: raise RuntimeError(f'check failed: {cmd}, exit={p.returncode}')
                finally:
                    try: os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    p.wait()
            print(log.read_text(), end='')
    except Exception as exc:
        failure = str(exc)
        for log in out.glob('*.log'):
            print(log.read_text()[-16000:], end='')
    report = {'ok': failure is None, 'error': failure, 'seconds': time.monotonic() - start,
              'peakGroupRSSMiB': peak / 1024 ** 2, 'artifactBytes': size, 'out': str(out)}
    (out / 'resources.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    if failure: sys.exit(1)


if __name__ == '__main__':
    main()
