#!/usr/bin/env python3
"""Linux synthetic-test supervisor: <20s, <=1GiB descendant RSS, <=5MB output."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

here = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='bc-fit-synthetic-') as tmp:
    env = {**os.environ, 'FIT_TEST_TMP': tmp, 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
           'OPENBLAS_NUM_THREADS': '1', 'CUDA_VISIBLE_DEVICES': '', 'PYTHONDONTWRITEBYTECODE': '1'}
    started, peak, disk_peak, ids = time.monotonic(), 0, 0, set()
    log = Path(tmp) / 'test.log'
    with log.open('wb') as stream:
        child = subprocess.Popen([sys.executable, '-B', str(here / 'tests.py')], env=env,
                                 stdout=stream, stderr=stream, start_new_session=True)
        try:
            while child.poll() is None:
                proc = {}
                for item in Path('/proc').iterdir():
                    if not item.name.isdigit():
                        continue
                    try:
                        fields = (item / 'stat').read_text().rsplit(')', 1)[1].split()
                        proc[int(item.name)] = (int(fields[1]), int(fields[21]) * os.sysconf('SC_PAGE_SIZE'))
                    except (OSError, ValueError, IndexError):
                        pass
                ids = {child.pid}
                while True:
                    extra = {pid for pid, (parent, _) in proc.items() if parent in ids} - ids
                    if not extra:
                        break
                    ids.update(extra)
                peak = max(peak, sum(proc.get(pid, (0, 0))[1] for pid in ids))
                size = 0
                for directory, _, files in os.walk(tmp):
                    for name in files:
                        try:
                            size += (Path(directory) / name).stat().st_size
                        except FileNotFoundError:
                            pass
                disk_peak = max(disk_peak, size)
                if time.monotonic() - started >= 19 or peak > 1024**3 or disk_peak > 5_000_000:
                    raise RuntimeError('Synthetic test resource bound exceeded')
                time.sleep(.02)
        finally:
            if child.poll() is None:
                for pid in ids | {child.pid}:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait(timeout=.5)
    print(log.read_text()[:20000], end='')
    print(f'Synthetic only: seconds={time.monotonic()-started:.3f}; peakRSSMiB={peak/1024**2:.2f}; peakOutputBytes={disk_peak}')
    raise SystemExit(child.returncode)
