#!/usr/bin/env python3
"""Run only synthetic Node contracts under <=20 s / 1 GiB aggregate RSS / 5 MB artifacts."""
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent


def constrain():
    # V8 reserves a large virtual address range even for a tiny loader fixture.
    # The parent measures aggregate resident memory instead of virtual reservations.
    resource.setrlimit(resource.RLIMIT_CPU, (18, 18))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (5_000_000, 5_000_000))
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])


def main():
    with tempfile.TemporaryDirectory(prefix='frozen-replay-synthetic-') as directory:
        out = Path(directory)
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': directory,
               'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
        env = {k: v for k, v in env.items() if not k.startswith(('ARK_', 'COOP_', 'STRONGHOLD_', 'NODE_'))}
        start, peak, size, error = time.monotonic(), 0, 0, None
        with (out / 'tests.log').open('wb') as log:
            child = subprocess.Popen(['node', '--max-old-space-size=256', '--test', '--test-concurrency=1',
                                      str(HERE / '../coop-replay/contracts.test.mjs'),
                                      str(HERE / 'contracts.test.mjs')], cwd=HERE,
                                     stdout=log, stderr=log, env=env, start_new_session=True, preexec_fn=constrain)
            try:
                while True:
                    # Protocol test children use their own sessions; aggregate all descendants too.
                    pending, seen = [child.pid], set()
                    while pending:
                        pid = pending.pop()
                        if pid in seen:
                            continue
                        seen.add(pid)
                        try:
                            pending.extend(map(int, Path(f'/proc/{pid}/task/{pid}/children').read_text().split()))
                        except FileNotFoundError:
                            pass
                    rss = 0
                    for pid in seen | {os.getpid()}:
                        try:
                            for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                                if line.startswith('VmRSS:'):
                                    rss += int(line.split()[1]) * 1024
                        except FileNotFoundError:
                            pass
                    peak = max(peak, rss)
                    current_size = 0
                    # Each test removes its own temporary tree while the supervisor samples.
                    # os.walk tolerates a directory disappearing before scandir enters it.
                    for parent, _, names in os.walk(out):
                        for name in names:
                            path = Path(parent) / name
                            try:
                                if path.is_file() and not path.is_symlink():
                                    current_size += path.stat().st_size
                            except FileNotFoundError:
                                pass
                    size = max(size, current_size)
                    if time.monotonic() - start >= 19:
                        error = '19-second work limit (20-second total budget)'
                    elif peak >= 1024 ** 3:
                        error = '1-GiB aggregate RSS limit'
                    elif size >= 5_000_000:
                        error = '5-MB artifact limit'
                    if error or child.poll() is not None:
                        break
                    time.sleep(0.02)
            finally:
                for pid in seen:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait()
        print((out / 'tests.log').read_text(), end='')
        report = dict(ok=error is None and child.returncode == 0, seconds=time.monotonic() - start,
                      peakAggregateRSSBytes=peak, peakArtifactBytes=size, exitCode=child.returncode, error=error,
                      limits=dict(seconds=20, rssBytes=1024 ** 3, artifactBytes=5_000_000))
        print(json.dumps(report))
        return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
