#!/usr/bin/env python3
"""Hard 20s / 1 GiB aggregate RSS supervisor for synthetic tests only (Linux)."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
env = {**os.environ, 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
       'CUDA_VISIBLE_DEVICES': '', 'PYTHONDONTWRITEBYTECODE': '1'}
start, peak = time.monotonic(), 0
p = subprocess.Popen([sys.executable, '-B', str(HERE / 'tests.py')], env=env, start_new_session=True)
try:
    while p.poll() is None:
        # Include nested fake protocol processes even though each has its own process group.
        proc = {}
        for item in Path('/proc').iterdir():
            if not item.name.isdigit():
                continue
            try:
                fields = (item / 'stat').read_text().rsplit(')', 1)[1].split()
                proc[int(item.name)] = (int(fields[1]), int(fields[21]) * os.sysconf('SC_PAGE_SIZE'))
            except (OSError, ValueError, IndexError):
                pass
        ids = {p.pid}
        while True:
            extra = {pid for pid, (parent, _) in proc.items() if parent in ids} - ids
            if not extra:
                break
            ids.update(extra)
        rss = sum(proc.get(pid, (0, 0))[1] for pid in ids)
        peak = max(peak, rss)
        if time.monotonic() - start >= 19.5 or rss > 1024**3:
            for pid in ids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise SystemExit('Synthetic test resource bound exceeded')
        time.sleep(.02)
    print(f'Synthetic contracts: exit={p.returncode}; seconds={time.monotonic()-start:.3f}; aggregatePeakRSSMiB={peak/1024**2:.2f}')
    raise SystemExit(p.returncode)
finally:
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
