#!/usr/bin/env python3
"""Fixed 8192-case train-only natural teacher collector. No training/evaluation."""
import argparse
import os
from pathlib import Path
import signal
import sys
import threading
import time

from reuse import (C, N, CODE, WORK_SECONDS, CLEANUP_SECONDS, REQUEST_BYTES, DISK_BYTES,
                   plan, source_identity, game_identity)
from storage import Budget, new_output, publish, sync_dir
from collector import clock, error_text, run_lanes
from reconcile import reconcile
from wire import Registry


def affinity():
    available = sorted(os.sched_getaffinity(0))
    C.require(available, 'No available CPUs')
    selected = available[:32]
    os.sched_setaffinity(0, selected)  # intersection only; never expand inherited taskset
    C.require(set(os.sched_getaffinity(0)) <= set(available) and len(selected) <= 32, 'CPU affinity bound')
    return selected


def emergency_rows(out, rows):
    """Failed/unfrozen fallback, preserving all known execution evidence without claiming hashes."""
    for p in list((out / 'journals').glob('*')) + list((out / 'cases').glob('*')):
        prefix = p.name.split('-', 1)[0]
        if prefix.isdigit() and 0 <= int(prefix) < len(rows):
            r = rows[int(prefix)]
            if r['status'] in ('not-run', 'started'):
                r.update(status='interrupted', eligiblePositive=False, error='Freeze incomplete; evidence exists')
        else:
            for r in rows:
                if r['status'] == 'not-run':
                    r.update(status='unknown', error='Unattributable evidence during failed freeze')
    # Without a completed independent reconciliation, absence is not proof of nonexecution.
    for r in rows:
        if r['status'] == 'not-run':
            r.update(status='unknown', error='Independent freeze did not complete')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    cpus = affinity()
    begin = time.monotonic()
    work_end, cleanup_end = begin+WORK_SECONDS, begin+WORK_SECONDS+CLEANUP_SECONDS
    root = args.root.resolve()
    out = new_output(args.out if args.out.is_absolute() else root / args.out)
    budget, stop, registry = Budget(), threading.Event(), Registry()
    rows = plan()
    manifest = dict(kind='natural-teacher-scale/v1', status='running', frozen=False, globalFailed=False,
                    trainingAllowed=False, inputReviewStatus='pending-main-independent-source-and-corpus-acceptance',
                    base='ffbe8cc6b994f0676cb386b7c9f88d9e8281333f', startedAt=clock(),
                    planned=8192, pairedTrainingSeeds=4096, seedSplit='train only 32001..36096',
                    workers=32, lanesPerPool=16, cpus=cpus, workSeconds=WORK_SECONDS,
                    cleanupSeconds=CLEANUP_SECONDS, totalSeconds=WORK_SECONDS+CLEANUP_SECONDS,
                    requestSeconds=120, requestWireLimit=REQUEST_BYTES,
                    aggregateWireLimit=8192*REQUEST_BYTES, diskLimit=DISK_BYTES,
                    primaryMetric='team victory; learner individual alive14 victory and rejectedLabels separate',
                    games=rows, lanes={}, globalErrors=[])
    for name in ('journals', 'cases', 'lanes'):
        (out / name).mkdir()
    sync_dir(out)
    publish(out / 'plan.json', dict(games=rows, planned=8192, split='train'), budget)
    publish(out / 'manifest.json', manifest, budget, snapshot=True)

    def interrupted(signum, _):
        stop.set()
        manifest['globalErrors'].append('Signal: ' + str(signum))

    def alarm(*_):
        stop.set()
        registry.kill()
        raise TimeoutError('Absolute work plus finite cleanup deadline')

    previous = {sig: signal.signal(sig, handler) for sig, handler in
                ((signal.SIGTERM, interrupted), (signal.SIGINT, interrupted), (signal.SIGALRM, alarm))}
    signal.setitimer(signal.ITIMER_REAL, max(.001, cleanup_end-time.monotonic()))
    attempted = False
    try:
        # Remove ambient game overrides before the static child too; no runtime module patches.
        for key in list(os.environ):
            if key.startswith(('ARK_', 'COOP_')):
                del os.environ[key]
        before_source = source_identity()
        game_path, game = game_identity(root)
        pool_hash = N.pool_sha(CODE)
        before = dict(source=before_source, game=game, poolSHA256=pool_hash)
        publish(out / 'source-before.json', before, budget)
        commands = {}
        for pool in ('normal', 'mixed'):
            env = C.clean_env()
            env.update(COOP_POOL=pool, COOP_SPLIT='train', COOP_POOL_SEED='313',
                       COOP_TEAM=','.join(C.TEAMS['original' if pool == 'normal' else 'mixed']),
                       UV_THREADPOOL_SIZE='1')
            commands[pool] = (['node', '--max-old-space-size=768', str(CODE / 'coop/env.mjs'),
                               '--game', str(game_path)], env)
        attempted = True
        try:
            run_lanes(out, rows, commands, game, pool_hash, budget, manifest, work_end, cleanup_end,
                      stop, registry=registry)
        except BaseException as e:
            stop.set()
            manifest['globalErrors'].append(error_text(e))
        C.require(not any(t.is_alive() for t in registry.workers), 'Live worker after cleanup; freeze forbidden')
        # Always attempt after checks, even if a request/lane failed.
        try:
            after = dict(source=source_identity(), game=game_identity(root)[1], poolSHA256=N.pool_sha(CODE))
            C.require(after == before, 'Source/rules/package/static schema drift')
        except Exception as e:
            manifest['globalErrors'].append(error_text(e))
            after = dict(status='failed', error=error_text(e))
        publish(out / 'source-after.json', after, budget)
        audit = reconcile(out, plan(), cleanup_end)
        rows = audit['games']
        manifest.update(games=rows, lanes=audit['lanes'], files=audit['files'], filesSHA256=audit['filesSHA256'])
        manifest['globalErrors'].extend(audit['errors'])
        manifest['frozen'] = True
    except BaseException as e:
        stop.set()
        manifest['globalErrors'].append(error_text(e))
        if attempted:
            manifest['games'] = emergency_rows(out, rows)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        manifest['globalErrors'].extend(registry.kill())
        manifest['globalFailed'] = bool(manifest['globalErrors'])
        manifest['status'] = ('failed' if manifest['globalFailed'] else
                              'complete' if all(r['status'] == 'complete' for r in manifest['games']) else 'incomplete')
        manifest.update(finishedAt=clock(), seconds=time.monotonic()-begin, diskBytesBeforeFinal=budget.used,
                        diskPeakBeforeFinal=budget.peak, trainingAllowed=False)
        publish(out / 'manifest.json', manifest, budget, snapshot=True)
        C.require(budget.used <= DISK_BYTES, 'Final disk budget')
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(C.encoded(dict(status=manifest['status'], frozen=manifest['frozen'], planned=8192,
                        complete=sum(r['status'] == 'complete' for r in manifest['games']),
                        manifest=str(out / 'manifest.json'), manifestSHA256=C.sha(out / 'manifest.json'),
                        trainingAllowed=False)).decode(), end='')
    return 0 if manifest['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
