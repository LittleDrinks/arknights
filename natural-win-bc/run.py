#!/usr/bin/env python3
"""Explicit finite train / dev-only eval; no collection or automatic posttrain games."""
import argparse
from pathlib import Path
import signal
import time

from support import (C, MIB, REVIEW, LIMITATIONS, Evidence, source_identity, game_identity,
                     check_parent, pool_sha)
from admission import admit, recheck
from evaluation import schedule, summary, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    train = sub.add_parser('train')
    ev = sub.add_parser('eval')
    for p in (train, ev):
        p.add_argument('--root', type=Path, required=True)
        p.add_argument('--experiment-code')
        p.add_argument('--parent', default='experiments/bc-expansion-v1/all.pt')
        p.add_argument('--parent-sha256', required=True)
        p.add_argument('--out', required=True)
        p.add_argument('--total-timeout', type=int, default=1100)
    train.add_argument('--input-dir', default='experiments/natural-coop-teacher-search-v1')
    train.add_argument('--manifest-sha256', required=True, help='Hash frozen by main AFTER collector exits')
    ev.add_argument('--trained', required=True)
    ev.add_argument('--dev-only', action='store_true', required=True)
    ev.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    ev.add_argument('--timeout', type=int, default=120)
    ev.add_argument('--output-mib', type=int, default=64)
    args = parser.parse_args()
    C.require(1 <= args.total_timeout <= 1100, 'Total timeout must be 1..1100s')
    if args.command == 'eval':
        C.require(1 <= args.timeout <= 120 and 1 <= args.output_mib <= 64, 'Per-game limits: <=120s, <=64MiB')
    start = time.monotonic()
    root = args.root.resolve()
    # Check the unresolved target first: never follow an existing output symlink.
    out = C.new_output(root / args.out)
    rows = schedule() if args.command == 'eval' else None
    report = dict(status='running', inputReviewStatus=REVIEW, limitations=LIMITATIONS)
    if rows is not None:
        C.write(out / 'manifest.json', dict(kind='natural-win-dev-eval/v1', denominator=64, games=rows,
                models=['parent', 'naturalWins'], families=['original', 'mixed'], heldoutFamilies=False,
                poolSeed=313, teams=C.TEAMS, split='eval', greedy=True, workers=args.workers,
                timeoutSeconds=args.timeout, totalTimeoutSeconds=args.total_timeout,
                outputBudgetPerGame=args.output_mib * MIB, maxProtocolBytesTotal=64 * args.output_mib * MIB,
                inputReviewStatus=REVIEW, limitations=LIMITATIONS))
    def alarm(*_):
        raise TimeoutError('Absolute CLI timeout')
    old = signal.signal(signal.SIGALRM, alarm)
    # The alarm covers admission, training and final guards too. Eval reserves time
    # inside this limit for game_job process cleanup and final source verification.
    signal.setitimer(signal.ITIMER_REAL, max(.001, args.total_timeout - (time.monotonic() - start)))
    try:
        code = C.code_root(root, args.experiment_code)
        parent = C.relative(root, args.parent)
        check_parent(root, parent, args.parent_sha256)
        source = source_identity(code)
        game_path, game = game_identity(root, code)
        def guard():
            C.require(source_identity(code) == source, 'Source drift')
            C.require(game_identity(root, code)[1] == game, 'Game/static-data drift')
            check_parent(root, parent, args.parent_sha256)
        if args.command == 'train':
            directory = C.relative(root, args.input_dir)
            evidence = Evidence(out)
            samples, metadata = admit(directory, args.manifest_sha256, game, pool_sha(code), evidence)
            L = C.learner(code)
            from training import train as fit
            report = fit(L, parent, samples, metadata, out, source, game, evidence)
            guard()
            recheck(directory, metadata['inputFiles'])
        else:
            L = C.learner(code)
            evaluate(L, C.relative(root, args.trained), parent, code, game_path, game, out, source, rows,
                     start + args.total_timeout - min(30, args.total_timeout / 10), guard,
                     workers=args.workers, timeout=args.timeout,
                     byte_limit=args.output_mib * MIB)
            report['status'] = 'complete' if all(r['status'] == 'complete' for r in rows) else 'failed'
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}'[:2000])
        if rows is not None:
            for row in rows:
                if row['status'] == 'complete':
                    row.update(status='failed', error='Run invalidated: ' + report['error'])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        if rows is not None:
            report.update(summary(rows), games=rows)
        C.write(out / ('train.json' if rows is None else 'summary.json'), report)
    print(f"{args.command}: {report['status']} -> {out}")
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
