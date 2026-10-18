#!/usr/bin/env python3
"""Two bounded server entrypoints: train and explicit dev-only eval."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from contracts_fit import (C, D, CODE, MODELS, MANIFEST_SHA, PARENT_SHA, MIB, LIMITATIONS,
    Evidence, check_parent, game_identity, identity, input_paths, project_off, recheck_inputs,
    safe_path, source_identity)
from evaluation import schedule, summary, evaluate, invalidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    train, ev = sub.add_parser('train'), sub.add_parser('eval')
    for p in (train, ev):
        p.add_argument('--root', type=Path, required=True)
        p.add_argument('--experiment-code', type=Path, default=CODE)
        p.add_argument('--out', type=Path, required=True)
        p.add_argument('--total-timeout', type=int, default=1100)
    train.add_argument('--input-dir', type=Path, default=Path('experiments/natural-gold-recapture-v1'))
    train.add_argument('--collector', type=Path, required=True)
    train.add_argument('--output-mib', type=int, default=512)
    ev.add_argument('--trained', type=Path, required=True)
    ev.add_argument('--dev-only', action='store_true', required=True)
    ev.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    ev.add_argument('--timeout', type=int, default=120)
    ev.add_argument('--output-mib', type=int, default=64)
    args = parser.parse_args(argv)
    C.require(sys.version_info >= (3, 10), 'Python 3.10+ required')
    C.require(1 <= args.total_timeout <= 1100 and 1 <= args.output_mib <=
              (512 if args.command == 'train' else 64), 'Time/output budget bounds')
    if args.command == 'eval':
        C.require(1 <= args.timeout <= 120, 'Per-game timeout 1..120')
    start = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    root = safe_path(args.root)
    out = safe_path(root / args.out)
    C.new_output(out)
    out_fd = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    out_stat = os.fstat(out_fd)
    rows = schedule() if args.command == 'eval' else None
    report = dict(status='running', kind='bc-fit-budget/v1' if rows is None else 'bc-fit-budget-dev/v1',
                  limitations=LIMITATIONS, creditGranted=False)
    if rows is not None:
        with (out / 'manifest.json').open('xb') as stream:
            stream.write(C.encoded(dict(kind='bc-fit-budget-dev/v1', games=rows, denominator=96, models=MODELS,
                split='eval', devSeeds=list(range(1, 17)), teams=C.TEAMS, poolSeed=313, teacher=False, greedy=True,
                cpuWorkers=args.workers, torchThreads=1, totalTimeoutSeconds=args.total_timeout,
                timeoutSeconds=args.timeout, outputBudgetPerGame=args.output_mib * MIB,
                maxGameBytesTotal=96 * args.output_mib * MIB, limitations=LIMITATIONS)))
        C.write(out / 'summary.json', dict(**report, **summary(rows), games=rows))

    def alarm(*_):
        raise TimeoutError('Absolute CLI timeout')

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, max(.001, args.total_timeout - (time.monotonic() - start)))
    try:
        # Linux CPU affinity limits the entire inherited process tree to at most four CPUs.
        affinity = sorted(os.sched_getaffinity(0))[:4]
        os.sched_setaffinity(0, affinity)
        code = safe_path(root / args.experiment_code)
        source = source_identity(code)
        parent = check_parent(root)
        version = subprocess.run(['node', '--version'], env=C.clean_env(), capture_output=True, timeout=5, check=True)
        match = re.fullmatch(rb'v(\d+)\.(\d+)\.(\d+)\n?', version.stdout)
        C.require(match is not None and int(match[1]) >= 22 and not version.stderr, 'Node >=22 required by game package')
        node = version.stdout.decode('ascii').strip()
        game_path, game, pin = game_identity(root, code)
        report.update(source=source, game=game, gameIdentity=pin, parentSHA256=PARENT_SHA,
                      manifestSHA256=MANIFEST_SHA, nodeVersion=node)

        def guard():
            safe_path(root)
            safe_path(out)
            current = out.stat()
            C.require((current.st_dev, current.st_ino) == (out_stat.st_dev, out_stat.st_ino), 'Output directory replaced')
            last_source = source_identity(code)
            C.require(last_source == source, 'Source drift')
            check_parent(root)
            last_game = game_identity(root, code)
            C.require(last_game == (game_path, game, pin), '486 rules/static vocabulary drift')
            return dict(sourceSHA256=last_source['sha256'], parentSHA256=PARENT_SHA,
                        gameIdentity=last_game[2], staticGame=last_game[1], outputDirectoryUnchanged=True)

        L = C.learner(code)
        if args.command == 'train':
            directory, collector = safe_path(root / args.input_dir), safe_path(root / args.collector)
            C.require(identity(directory / 'manifest.json', 8 * MIB)['sha256'] == MANIFEST_SHA, 'Frozen manifest SHA')
            paths = input_paths(directory) + [str(collector)]
            evidence = Evidence(out, args.output_mib * MIB - MIB)
            evidence.json('admission-paths.json', paths)
            paired, metadata = D.admit(directory, MANIFEST_SHA, code, collector, game, pin, evidence)
            samples = project_off(paired, metadata, game)
            del paired
            from training import fit
            report = fit(L, parent, samples, metadata, source, game, pin, evidence)
            report['nodeVersion'] = node
            final_guard = guard()
            recheck_inputs(metadata, paths)
            evidence.verify()
            report['finalChecks'] = dict(**final_guard, inputEvidenceVerified=True, publishedEvidenceVerified=True)
        else:
            report['finalChecks'] = evaluate(L, safe_path(root / args.trained), parent, code, game_path, game, pin, out, source, rows,
                start + args.total_timeout - min(30, args.total_timeout / 10), guard,
                workers=args.workers, timeout=args.timeout, byte_limit=args.output_mib * MIB)
            report['status'] = 'complete' if all(r['status'] == 'complete' for r in rows) else 'failed'
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}'[:2000])
        if rows is not None:
            invalidate(rows, report['error'])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        report.update(startedAt=started, finishedAt=datetime.now(timezone.utc).isoformat(),
            seconds=time.monotonic() - start,
            limits=dict(totalSeconds=args.total_timeout, outputMiB=args.output_mib, cpuMax=4))
        try:
            current = safe_path(out).stat()
            C.require((current.st_dev, current.st_ino) == (out_stat.st_dev, out_stat.st_ino), 'Output directory replaced')
            safe_path(out / ('train.json' if rows is None else 'summary.json'))
        except Exception as error:
            report.update(status='failed', error=str(error)[:2000])
            if rows is not None:
                invalidate(rows, report['error'])
        report['creditGranted'] = report['status'] == 'complete'
        if rows is not None:
            report.update(summary(rows), games=rows)
        raw = C.encoded(report)
        C.require(len(raw) <= (MIB if rows is None else 4 * MIB), 'Final report reserve')
        # Publish through the original directory descriptor even if its pathname was
        # replaced. Never follow the replacement symlink to publish success elsewhere.
        name = 'train.json' if rows is None else '.final-summary.json'
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=out_fd)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
            if rows is not None:
                os.replace(name, 'summary.json', src_dir_fd=out_fd, dst_dir_fd=out_fd)
        finally:
            os.close(out_fd)
    print(f"{args.command}: {report['status']} -> {out}")
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
