#!/usr/bin/env python3
"""Eval-only, predeclared dev1..256; fixed parent versus completed F steps3000."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time

from contracts_wide import (C, F, P, CODE, HERE, BASE, PINS, MODELS, MIB, TOTAL_BYTES, META_BYTES,
                            LIMITATIONS, Guard, digest, require, safe, source_identity, write_new)
from admission_wide import admit, sha_argument
from evaluation import schedule, summary, dispatch, audit, invalidate


class Deadline(BaseException):
    pass


def execute(args):
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    require(sys.version_info >= (3, 10) and sys.platform.startswith('linux'), 'Python3.10+ Linux required')
    require(1 <= args.workers <= 16 and 1 <= args.timeout <= 120 and 1 <= args.total_timeout <= 2400 and
            1 <= args.output_mib <= 64, 'Finite CLI resource bounds')
    sha_argument(args.candidate_sha256)
    sha_argument(args.train_json_sha256)
    root, code = safe(args.root), safe(args.root / args.experiment_code)
    require(code == CODE, '--experiment-code must be the actual CLI checkout')
    out, trained = safe(root / args.out), safe(root / args.trained)
    parent = safe(root / F.PARENT)
    protected = [code, root / 'game', trained, parent.parent]
    metadata_path = trained / 'input-metadata.json'
    if metadata_path.exists():
        metadata = F.read_json(metadata_path)
        protected.extend(safe(p) for p in metadata['rawFileSets'])
        # A collector may live directly in ROOT. Protect each bound file, not
        # its entire parent; raw input directories are separately protected.
        protected.extend(safe(p) for p in metadata['inputFiles'])
    require(all(out != p and p not in out.parents and out not in p.parents for p in protected),
            'Output overlaps source/game/training/input/checkpoint paths')
    C.new_output(out)
    fd = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    output_guard = Guard()
    output_guard.path(out)
    rows, seals, controls = schedule(), {}, {}
    report = dict(kind='wide-dev/v1', base=BASE, status='failed', creditGranted=False, integrity='rejected',
                  limitations=LIMITATIONS, startedAt=started_at)
    deadline = start + args.total_timeout
    # Leave time for child cleanup and auditing the accumulated evidence. The
    # absolute alarm also covers admission, guard hashing and static vocab reload.
    game_deadline = deadline - min(60, args.total_timeout / 10)
    limits = dict(workers=args.workers, cpuMax=16, torchThreads=1, gameSeconds=args.timeout,
                  totalSeconds=args.total_timeout, perGameBytes=args.output_mib * MIB,
                  aggregateBytes=TOTAL_BYTES, metadataReservedBytes=META_BYTES, maxSteps=4096)
    manifest = dict(kind='wide-dev/v1', base=BASE, games=schedule(), denominator=2048, pairedSeedGroups=256,
                    devSeeds=list(range(1, 257)), split='eval', developmentOnly=True,
                    unseenSeedsClaim=False, reservedSeeds='90017..90048 and 100001 onward unused',
                    models=MODELS, controllers=P.VERSIONS, teams=C.TEAMS, poolSeed=313,
                    greedy=True, teacher=False, candidateSHA256=args.candidate_sha256,
                    trainJsonSHA256=args.train_json_sha256, parentSHA256=F.PARENT_SHA,
                    code=str(code), trained=str(trained), limits=limits, limitations=LIMITATIONS)
    previous = signal.getsignal(signal.SIGALRM)
    def alarm(*_):
        raise Deadline('Absolute 2400s-or-lower CLI deadline')
    try:
        # All 2048 rows are durably declared BEFORE any subprocess (including git/node).
        controls['manifest.json'] = write_new(out / 'manifest.json', manifest, 4 * MIB)
        output_guard.file(out / 'manifest.json')
        signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, max(.000001, deadline - time.monotonic()))
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:16])
        report['cpuAffinity'] = sorted(os.sched_getaffinity(0))
        F.check_parent(root)  # Wrong/missing parent fails before Torch/Node/static loader.
        source = source_identity(code)
        source_guard = Guard()
        source_guard.path(code / '.git')
        if (code / '.git').is_file():
            source_guard.file(code / '.git')
        source_guard.directory(HERE)
        for folder in PINS['trees']:
            source_guard.directory(code / folder)
        for name in PINS['files']:
            source_guard.file(code / name)
        source_guard.file(parent)
        version = subprocess.run(['node', '--version'], env=C.clean_env(), capture_output=True, timeout=5, check=True)
        match = re.fullmatch(rb'v(\d+)\.(\d+)\.(\d+)\n?', version.stdout)
        require(match is not None and int(match[1]) >= 22 and not version.stderr, 'Node >=22 required')
        game_path, game, pin = F.game_identity(root, code)
        game_guard = Guard()
        game_guard.path(game_path)
        game_guard.path(game_path / '.git')
        if (game_path / '.git').is_file():
            game_guard.file(game_path / '.git')
        for name in F.read_json(code / 'policy/pins/rules-manifest.json')['files']:
            game_guard.file(game_path / name)
        L = C.learner(code)
        require(L.torch.get_num_threads() == L.torch.get_num_interop_threads() == 1, 'Torch thread1 required')
        models, admitted, verify_training = admit(L, trained, parent, args.candidate_sha256,
            args.train_json_sha256, source['fit'], game, pin)
        controls['admission.json'] = write_new(out / 'admission.json', dict(**admitted, source=source,
            game=game, gameIdentity=pin, nodeVersion=version.stdout.decode().strip()), 4 * MIB)
        output_guard.file(out / 'admission.json')
        report['admission'] = admitted
        worker_config = dict(affinity=report['cpuAffinity'], source=source, game=game, gamePath=str(game_path),
            out=str(out), deadline=deadline, models=admitted['models'],
            paths={'parent': str(parent), 'steps3000': str(trained / 'steps3000.pt')},
            files={'parent': digest(parent), 'steps3000': admitted['candidate']})
        seals, errors = dispatch(rows, models, game_path, out, game_deadline, args.workers,
                                 args.timeout, args.output_mib * MIB, F.G.pool_sha(code), worker_config=worker_config)
        # Audit every category even following a scheduler integrity failure.
        checks = {}
        guards = {
            'output': lambda: output_guard.verify(deadline),
            'sourceInodes': lambda: source_guard.verify(deadline),
            'source': lambda: require(source_identity(code) == source, 'Source/HEAD drift'),
            'parent': lambda: F.check_parent(root),
            'gameInodes': lambda: game_guard.verify(deadline),
            'game486': lambda: require(F.game_identity(root, code) == (game_path, game, pin), 'Game/static vocabulary drift'),
            'inputModelTrainingEvidence': lambda: verify_training(deadline),
            'payloadResults': lambda: audit(out, rows, seals, controls, args.output_mib * MIB, deadline),
        }
        for name, check in guards.items():
            try:
                require(time.monotonic() < deadline, 'Final guard deadline')
                check()
                checks[name] = True
            except BaseException as exc:
                checks[name] = False
                errors.append(name + ': ' + f'{type(exc).__name__}: {exc}'[:2000])
        report['finalChecks'] = checks
        require(not errors, '; '.join(errors)[:12000])
        report.update(integrity='verified', status='complete' if all(r['status'] == 'complete' for r in rows) else 'failed')
    except BaseException as exc:
        report.update(status='failed', integrity='rejected', error=f'{type(exc).__name__}: {exc}'[:12000])
        invalidate(rows, report['error'])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        try:
            output_guard.verify(float('inf'))
        except Exception as exc:
            report.update(status='failed', integrity='rejected', error=str(exc)[:2000])
            invalidate(rows, report['error'])
        report.update(creditGranted=report['status'] == 'complete', **summary(rows), games=rows,
                      evidence=seals, limits=limits, seconds=time.monotonic() - start,
                      finishedAt=datetime.now(timezone.utc).isoformat())
        raw = C.encoded(report)
        try:
            require(len(raw) <= 24 * MIB, 'Final summary reserve')
            # Original descriptor: a replaced pathname cannot redirect a success report.
            temporary = '.summary-' + secrets.token_hex(8) + '.tmp'
            target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(target, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, 'summary.json', src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            os.close(fd)
    print(f"{report['status']}: {report['complete']}/2048 complete; integrity={report['integrity']} -> {out}")
    return 0 if report['status'] == 'complete' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    ev = sub.add_parser('eval')
    ev.add_argument('--dev-only', action='store_true', required=True)
    ev.add_argument('--root', type=Path, required=True)
    ev.add_argument('--experiment-code', type=Path, default=CODE)
    ev.add_argument('--trained', type=Path, required=True)
    ev.add_argument('--candidate-sha256', required=True)
    ev.add_argument('--train-json-sha256', required=True)
    ev.add_argument('--out', type=Path, required=True)
    ev.add_argument('--workers', type=int, choices=range(1, 17), default=16)
    ev.add_argument('--timeout', type=int, default=120)
    ev.add_argument('--total-timeout', type=int, default=2400)
    ev.add_argument('--output-mib', type=int, default=64)
    args = parser.parse_args(argv)
    try:
        return execute(args)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
