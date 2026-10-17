#!/usr/bin/env python3
"""Linux local verification supervisor: sample SUM of RSS for self and entire descendant tree."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from common import HERE, json_bytes, output_dir, write_json


def process_tree(root, known):
    processes = {}
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            fields = dict(line.split(':', 1) for line in path.read_text().splitlines() if ':' in line)
            processes[int(path.parent.name)] = (int(fields['PPid']), int(fields.get('VmRSS', '0 kB').split()[0]) * 1024)
        except (OSError, ValueError, KeyError):
            continue
    included = {root} | (known & processes.keys())
    while True:
        added = {pid for pid, (ppid, _) in processes.items() if ppid in included} - included
        if not added:
            break
        included.update(added)
    return included, {pid: processes.get(pid, (0, 0))[1] for pid in included}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--real-only', action='store_true')
    args = parser.parse_args()
    out = output_dir(args.out)
    log = out / 'run.log'
    command = [sys.executable, '-B', str(HERE / 'check.py'), '--out', str(out / 'artifacts')]
    if args.real_only:
        command.append('--real-only')
    rss_limit, output_limit, wall_limit = 1024 ** 3, 10_000_000, 20 if args.real_only else 50
    start = time.monotonic()
    known, peak, samples, max_gap, last_sample, peak_breakdown = set(), 0, 0, 0, start, {}
    reason = None
    with log.open('wb') as stream:
        child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                 env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1',
                                      'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'})
        while True:
            now = time.monotonic()
            known, breakdown = process_tree(os.getpid(), known)
            rss = sum(breakdown.values())
            if rss > peak:
                peak, peak_breakdown = rss, breakdown
            samples += 1
            max_gap, last_sample = max(max_gap, now - last_sample), now
            size = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
            if rss > rss_limit:
                reason = 'process-tree RSS exceeded 1 GiB'
            elif size > output_limit - 8192:
                reason = 'output exceeds reserved 10 MB budget'
            elif now - start > wall_limit:
                reason = 'verification wall deadline'
            if reason or child.poll() is not None:
                break
            time.sleep(.01)
        # Includes engine children in separate sessions; retain known orphan PIDs.
        remaining, _ = process_tree(os.getpid(), known)
        for pid in remaining - {os.getpid()}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        code = child.wait()
    size = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    report = {'command': command, 'exitCode': code, 'terminationReason': reason,
              'seconds': time.monotonic() - start, 'rssLimitBytes': rss_limit,
              'peakSummedTreeRSSBytes': peak, 'peakProcessRSSBytes': peak_breakdown,
              'rssSamples': samples, 'maxSampleGapSeconds': max_gap,
              'measurement': 'Linux /proc VmRSS summed across supervisor and all descendants; sampled, not kernel-enforced instantaneous peak',
              'artifactBytesBeforeReport': size, 'outputLimitBytes': output_limit,
              'passedResourceChecks': reason is None and peak <= rss_limit and size + 8192 <= output_limit}
    write_json(out / 'resources.json', report)
    print(json_bytes(report).decode(), end='')
    print(log.read_text()[-5000:])
    return 0 if code == 0 and report['passedResourceChecks'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
