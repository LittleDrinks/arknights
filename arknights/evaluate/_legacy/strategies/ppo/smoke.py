#!/usr/bin/env python3
"""Linux stdlib supervisor: <=25s aggregate checks, <2 GiB RSS, <=20 MB artifacts.

Runs numerical tests, real BC preparation, workers=1/2 PPO, checkpoint reload.
No Torch in this monitor process. Any limit stops the test with a failure record.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
RSS_LIMIT = 1900 * 1024 * 1024  # Headroom below the user's 2 GiB ceiling.
BYTE_LIMIT = 20_000_000


def process_table():
    table = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            table[int(path.parent.name)] = (int(fields[1]), int(fields[2]),
                                            int(fields[21]) * os.sysconf('SC_PAGE_SIZE'))
        except (OSError, ValueError, IndexError):
            pass
    return table


def descendants(table, known):
    found = set(known)
    while True:
        more = {pid for pid, (parent, _group, _rss) in table.items() if parent in found}
        if more <= found:
            return found
        found |= more


def terminate(process, known):
    # First give the learner time to reap actors and their Node subprocesses.
    if process.poll() is None:
        process.terminate()
    deadline = time.monotonic() + .7
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(.01)
    table = process_table()
    known = descendants(table, known)
    for pid in known:
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', help='Reuse an existing hidden=8 BC checkpoint instead of running demo')
    parser.add_argument('--skip-numeric', action='store_true', help='Only if already checked within this budget')
    parser.add_argument('--seconds', type=float, default=25, help='Aggregate deadline, at most 25 seconds')
    args = parser.parse_args()
    if not 0 < args.seconds <= 25:
        parser.error('seconds must be in (0,25]')
    out = Path(tempfile.mkdtemp(prefix='parallel-rl-smoke-'))
    start = time.monotonic()
    deadline = start + args.seconds
    report = {'output': str(out), 'rssLimitBytes': RSS_LIMIT, 'checks': [], 'peakRSSBytes': 0}
    jobs = [
        ('numeric', ['test_numeric.py']),
        ('prepare', ['smoke_fixture.py', 'prepare', str(out)]),
        ('workers1', ['train.py', '--workers', '1', '--steps', '4', '--rollout', '2']),
        ('workers2', ['train.py', '--workers', '2', '--steps', '17', '--rollout', '4']),
        ('reload', ['smoke_fixture.py', 'verify', str(out)]),
    ]
    if args.skip_numeric:
        jobs = [job for job in jobs if job[0] != 'numeric']
    if args.checkpoint:
        if Path(args.checkpoint).stat().st_size > 1_000_000:
            parser.error('Use a small hidden=8 checkpoint for the local RSS budget')
        shutil.copyfile(args.checkpoint, out / 'bc.pt')
        config = json.loads((HERE.parent / 'rl/config.json').read_text())
        config.update(hidden=8, seed=2001, epochs=1, batch_size=4, rollout_steps=4, prep_budget=1)
        (out / 'config.json').write_text(json.dumps(config))
        jobs = [job for job in jobs if job[0] != 'prepare']
        report['existingCheckpoint'] = str(Path(args.checkpoint).resolve())
    known = {os.getpid()}
    failed = False
    for label, arguments in jobs:
        if label.startswith('workers'):
            workers = label[-1]
            arguments += ['--checkpoint', str(out / 'bc.pt'), '--config', str(out / 'config.json'),
                          '--out', str(out / f'w{workers}.pt'), '--timeout', '5', '--batch-timeout', '8']
        if time.monotonic() >= deadline:
            report['checks'].append({'name': label, 'status': 'not-run', 'reason': 'aggregate deadline'})
            failed = True
            break
        job_start = time.monotonic()
        with (out / f'{label}.log').open('w') as log:
            process = subprocess.Popen([sys.executable, *arguments], cwd=HERE, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                                            'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'})
            known.add(process.pid)
            reason, peak = None, 0
            while process.poll() is None:
                table = process_table()
                known = descendants(table, known)
                rss = sum(table[pid][2] for pid in known if pid in table)
                peak = max(peak, rss)
                if rss >= RSS_LIMIT:
                    reason = 'RSS guard reached; no Node heap modification'
                elif time.monotonic() >= deadline:
                    reason = f'aggregate {args.seconds}-second deadline'
                elif sum(p.stat().st_size for p in out.glob('*') if p.is_file()) > BYTE_LIMIT:
                    reason = 'artifact size limit'
                if reason:
                    terminate(process, known)
                    break
                time.sleep(.01)
        report['peakRSSBytes'] = max(report['peakRSSBytes'], peak)
        result = {'name': label, 'exitCode': process.returncode, 'seconds': time.monotonic() - job_start,
                  'peakRSSBytes': peak, 'status': 'failed' if reason or process.returncode else 'passed'}
        if reason:
            result['reason'] = reason
        if label.startswith('workers') and result['status'] == 'passed':
            workers = int(label[-1])
            manifest = json.loads((out / f'w{workers}.pt.manifest.json').read_text())
            rows = [json.loads(line) for line in (out / f'w{workers}.pt.jsonl').read_text().splitlines()]
            assert manifest['status'] == 'complete'
            assert sum(row['environmentSteps'] for row in rows) == (4 if workers == 1 else 17)
            assert all(row['parameterDeltaL2'] > 0 for row in rows)
            assert all(row['behaviorSHA256'] == rows[i - 1]['parameterSHA256']
                       for i, row in enumerate(rows) if i)
            table = process_table()
            assert all(pid not in table for pid in manifest['actorPids'])
            assert all(actor['nodePid'] not in table for actor in manifest['actors'])
            result.update(totalSteps=manifest['totalSteps'], updates=len(rows),
                          completedEpisodes=manifest['completedEpisodes'],
                          parameterDeltaL2=[row['parameterDeltaL2'] for row in rows],
                          actorsAndNodesExited=True)
        report['checks'].append(result)
        print(json.dumps(result), flush=True)
        if result['status'] == 'failed':
            failed = True
            break
    report['seconds'] = time.monotonic() - start
    report['artifactBytes'] = sum(p.stat().st_size for p in out.glob('*') if p.is_file())
    report['status'] = 'failed' if failed else 'passed'
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
