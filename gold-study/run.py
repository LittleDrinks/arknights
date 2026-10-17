#!/usr/bin/env python3
"""Finite server CLI. Training consumes completed recapture; eval is separately explicit."""
import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
import signal
import subprocess
import sys
import time

from contracts import (C, N, R, MIB, CONFIG, MODES, REVIEW, LIMITATIONS, Evidence,
                       source_identity, schema_dimensions, recheck)
from data import admit
from evaluation import schedule, summary, evaluate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    train = sub.add_parser('train')
    ev = sub.add_parser('eval')
    for p in (train, ev):
        p.add_argument('--root', type=Path, required=True, help='Server root, e.g. /data1/zsm/arknights')
        p.add_argument('--experiment-code', help='Code path relative to root, or absolute')
        p.add_argument('--parent', required=True, help='Explicit experiments/bc-expansion-v1/all.pt')
        p.add_argument('--parent-sha256', required=True)
        p.add_argument('--out', required=True, help='New directory only')
        p.add_argument('--total-timeout', type=int, default=1100)
    train.add_argument('--input-dir', required=True, help='Completed paired recapture directory')
    train.add_argument('--manifest-sha256', required=True, help='Explicit frozen recapture manifest SHA256')
    train.add_argument('--collector', required=True, help='Original collector source, for actual SHA checks')
    train.add_argument('--output-mib', type=int, default=512, help='Aggregate train evidence budget')
    ev.add_argument('--trained', required=True)
    ev.add_argument('--dev-only', action='store_true', required=True)
    ev.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    ev.add_argument('--timeout', type=int, default=120)
    ev.add_argument('--output-mib', type=int, default=64, help='Per-game protocol/action/terminal byte budget')
    args = parser.parse_args(argv)
    C.require(sys.version_info >= (3, 10), 'Python 3.10+ required')
    C.require(1 <= args.total_timeout <= 1100, 'Total timeout 1..1100 seconds')
    C.require(1 <= args.output_mib <= (512 if args.command == 'train' else 64), 'Output budget bounds')
    if args.command == 'eval':
        C.require(1 <= args.timeout <= 120, 'Per-game timeout 1..120 seconds')
    start, clock = time.monotonic(), lambda: datetime.now(timezone.utc).isoformat()
    root = args.root.resolve()
    out = C.new_output(root / args.out)
    rows = schedule() if args.command == 'eval' else None
    report = dict(status='running', inputReviewStatus=REVIEW, limitations=LIMITATIONS)
    started_at = clock()
    if rows is not None:
        with (out / 'manifest.json').open('xb') as f:
            f.write(C.encoded(dict(kind='gold-study-dev-eval/v1', denominator=96, games=rows, models=MODES,
                split='eval', devSeeds=list(range(1, 17)), independentSeedGroups=16, teams=C.TEAMS,
                poolSeed=313, greedy=True, cpuWorkers=args.workers, torchThreadsPerWorker=1,
                totalTimeoutSeconds=args.total_timeout, timeoutSeconds=args.timeout,
                outputBudgetPerGame=args.output_mib * MIB, maxProtocolBytesTotal=96 * args.output_mib * MIB,
                inputReviewStatus=REVIEW, limitations=LIMITATIONS)))

    def alarm(*_):
        raise TimeoutError('Absolute CLI timeout')

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, max(.001, args.total_timeout - (time.monotonic() - start)))
    try:
        code = C.code_root(root, args.experiment_code)
        source = source_identity(code)
        parent = C.relative(root, args.parent)
        N.check_parent(root, parent, args.parent_sha256)
        version = subprocess.run(['node', '--version'], env=C.clean_env(), capture_output=True, timeout=5, check=True)
        node_version = re.fullmatch(rb'v(\d+)\.(\d+)\.(\d+)\n?', version.stdout)
        C.require(node_version is not None and int(node_version[1]) >= 22 and not version.stderr,
                  'Node >=22 required by the fixed game package')
        report['nodeVersion'] = version.stdout.decode('ascii').strip()
        game_path = root / 'game'
        pin = R.game_identity(code, game_path)
        resolved_game, game = C.game_identity(root, code)  # Static vocabulary only; no Match/Battle.
        C.require(resolved_game == game_path, 'Expected ROOT/game')
        old, dims, prefix = schema_dimensions({**game['schema'], 'version': R.VERSION})
        C.require(dims == [6410, 419] and dims[0] - prefix == 1143, 'Frozen full vocabulary dimensions')

        def guard():
            C.require(source_identity(code) == source, 'Source drift')
            N.check_parent(root, parent, args.parent_sha256)
            C.require(R.game_identity(code, game_path) == pin and C.game_identity(root, code)[1] == game,
                      '486-file rules/static vocabulary drift')

        L = C.learner(code)
        C.require(L.torch.get_default_dtype() == L.torch.float32, 'Original forward requires default float32')
        if args.command == 'train':
            evidence = Evidence(out, args.output_mib * MIB - MIB)
            samples, metadata = admit(C.relative(root, args.input_dir), args.manifest_sha256, code,
                                     C.relative(root, args.collector), game, pin, evidence)
            from training import fit
            report = fit(L, parent, samples, metadata, source, game, evidence)
            report['nodeVersion'] = version.stdout.decode('ascii').strip()
            guard()
            recheck(metadata)
            evidence.verify()
        else:
            evaluate(L, C.relative(root, args.trained), parent, code, game_path, game, out, source, rows,
                     start + args.total_timeout - min(30, args.total_timeout / 10), guard,
                     workers=args.workers, timeout=args.timeout, byte_limit=args.output_mib * MIB)
            report['status'] = 'complete' if all(r['status'] == 'complete' for r in rows) else 'failed'
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}'[:2000])
        if rows is not None:
            for row in rows:
                if row['status'] == 'complete':
                    row.update(status='failed', error='Run invalidated: ' + report['error'])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        report.update(startedAt=started_at, finishedAt=clock(), seconds=time.monotonic() - start,
                      limits=dict(totalSeconds=args.total_timeout, outputMiB=args.output_mib))
        if rows is None:
            raw = C.encoded(report)
            C.require(len(raw) <= MIB, 'Final training report reserve')
            with (out / 'train.json').open('xb') as f:
                f.write(raw)
        else:
            report.update(summary(rows), games=rows)
            C.write(out / 'summary.json', report)
    print(f"{args.command}: {report['status']} -> {out}")
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
