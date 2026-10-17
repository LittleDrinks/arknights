#!/usr/bin/env python3
"""Run only tests.py under <=20 s / 1 GiB aggregate RSS / 5 MiB artifacts."""
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
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (5 * 1024 ** 2, 5 * 1024 ** 2))
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])


def main():
    with tempfile.TemporaryDirectory(prefix='coop-items-synthetic-') as directory:
        out = Path(directory)
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'COOP_ITEMS_TEST_TMP': directory,
               'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
        start, peak, size, error = time.monotonic(), 0, 0, None
        with (out / 'tests.log').open('wb') as log:
            child = subprocess.Popen([sys.executable, '-B', str(HERE / 'tests.py'), '-v'], cwd=HERE,
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
                    for path in out.rglob('*'):
                        try:
                            if path.is_file() and not path.is_symlink():
                                current_size += path.stat().st_size
                        except FileNotFoundError:
                            pass
                    size = max(size, current_size)
                    if time.monotonic() - start >= 20:
                        error = '20-second limit'
                    elif peak >= 1024 ** 3:
                        error = '1-GiB aggregate RSS limit'
                    elif size >= 5 * 1024 ** 2:
                        error = '5-MiB artifact limit'
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
                      limits=dict(seconds=20, rssBytes=1024 ** 3, artifactBytes=5 * 1024 ** 2))
        print(json.dumps(report))
        return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
