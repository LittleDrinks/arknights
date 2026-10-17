#!/usr/bin/env python3
"""Supervise synthetic-only tests: 19s deadline, 1GiB tree RSS, 5MB temporary bytes."""
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time


def usage(pid):
    processes = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            processes[int(path.parent.name)] = (int(fields[1]), int(fields[21]) * os.sysconf('SC_PAGE_SIZE'))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    family = {pid}
    while True:
        found = {p for p, (parent, _) in processes.items() if parent in family} - family
        if not found:
            return family, sum(processes.get(p, (0, 0))[1] for p in family)
        family |= found


def main():
    start = time.monotonic()
    peak_rss, peak_disk, family = 0, 0, set()
    with tempfile.TemporaryDirectory(prefix='wide-dev-synthetic-') as directory:
        tmp = Path(directory)
        env = {**os.environ, 'WIDE_TEST_TMP': directory, 'TMPDIR': directory, 'PYTHONDONTWRITEBYTECODE': '1',
               'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'CUDA_VISIBLE_DEVICES': ''}
        def limits():
            resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
            resource.setrlimit(resource.RLIMIT_FSIZE, (5_000_000, 5_000_000))
        log = tmp / 'tests.log'
        with log.open('wb') as stream:
            child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).absolute().with_name('tests.py'))],
                stdout=stream, stderr=stream, env=env, start_new_session=True, preexec_fn=limits)
            try:
                while True:
                    pids, rss = usage(child.pid)
                    family |= pids
                    peak_rss = max(peak_rss, rss)
                    size = 0
                    for path in tmp.rglob('*'):
                        try:
                            if path.is_file() and not path.is_symlink():
                                size += path.stat().st_size
                        except FileNotFoundError:
                            pass
                    peak_disk = max(peak_disk, size)
                    if time.monotonic() - start >= 19 or peak_rss > 1024 ** 3 or peak_disk > 5_000_000:
                        raise RuntimeError('Synthetic budget exceeded')
                    if child.poll() is not None:
                        break
                    time.sleep(.01)
            finally:
                if child.poll() is None:
                    for pid in family | {child.pid}:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    child.wait(timeout=.5)
        print(log.read_text()[:20000], end='')
        print(f'Synthetic only: seconds={time.monotonic()-start:.3f}; peakRSSMiB={peak_rss/1024**2:.2f}; peakOutputBytes={peak_disk}')
        return child.returncode


if __name__ == '__main__':
    raise SystemExit(main())
