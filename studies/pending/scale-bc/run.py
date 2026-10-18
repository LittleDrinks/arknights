#!/usr/bin/env python3
"""Explicit admission-only / paired BC training. Never collect or evaluate games."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import resource
import signal
import time

from core import (C, S, CODE, MIB, CONFIG, OLD_SHA, PARENT_SHA, REFERENCE_SHA,
                  Evidence, file_id, source_identity, game_identity, parent_path, require, safe)
from admission_scale import (admit_all, bound_json, header, receipt, recheck, arm_labels, exact)


class Budgets:
    """Separate cumulative full-audit and other-work wall budgets, both finite."""
    def __init__(self, work, audit):
        self.remaining = dict(work=work, audit=audit)
        self.used = dict(work=0., audit=0.)
        self.previous = signal.signal(signal.SIGALRM, self.timeout)

    @staticmethod
    def timeout(*_):
        raise TimeoutError('Finite phase wall deadline')

    @contextmanager
    def phase(self, name):
        require(self.remaining[name] > 0, 'Exhausted phase budget')
        start = time.monotonic()
        deadline = start+self.remaining[name]
        signal.setitimer(signal.ITIMER_REAL, self.remaining[name])
        try:
            yield deadline
            require(time.monotonic() < deadline, 'Phase deadline exceeded')
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            elapsed = time.monotonic()-start
            self.remaining[name] -= elapsed
            self.used[name] += elapsed

    def close(self):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous)


def disjoint(out, inputs):
    for path in inputs:
        require(out != path and out not in path.parents and path not in out.parents,
                'Output overlaps protected input/source tree: ' + str(path))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('admission-only', 'train'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--new-input', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--source-acceptance-receipt', type=Path, required=True)
    p.add_argument('--receipt-sha256', required=True)
    p.add_argument('--old-input', type=Path, required=True)
    p.add_argument('--old-reference', type=Path, default=Path('experiments/bc-fit-budget-train-v1'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--arms', choices=('all', 'newOnly', 'combined', 'old6Reference'), default='all')
    p.add_argument('--work-seconds', type=int, default=900)
    p.add_argument('--audit-seconds', type=int, default=1800)
    p.add_argument('--ram-gib', type=int, default=16)
    p.add_argument('--output-mib', type=int, default=256)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    require(1 <= args.work_seconds <= 7200 and 1 <= args.audit_seconds <= 7200 and
            1 <= args.ram_gib <= 16 and 16 <= args.output_mib <= 1024, 'Finite resource limits')
    require(all(re.fullmatch('[0-9a-f]{64}', s) for s in (args.manifest_sha256, args.receipt_sha256)),
            'Explicit full bound SHA256 values required')
    start = time.monotonic()
    budgets = Budgets(args.work_seconds, args.audit_seconds)
    evidence = None
    report = dict(kind='scale-bc/v1', status='failed', command=args.command,
                  config=CONFIG, evaluationExecuted=False, creditGranted=False)
    try:
        with budgets.phase('work'):
            # At most parent + one sequential static child. Half the aggregate RAM
            # allowance as inherited address-space ceiling bounds their combined RSS.
            # Training has no children; deliberately retain this conservative ceiling.
            cap = args.ram_gib*1024*MIB//2
            old_soft, old_hard = resource.getrlimit(resource.RLIMIT_AS)
            cap = min(cap, old_soft) if old_soft != resource.RLIM_INFINITY else cap
            cap = min(cap, old_hard) if old_hard != resource.RLIM_INFINITY else cap
            resource.setrlimit(resource.RLIMIT_AS, (cap, old_hard))
            affinity = sorted(os.sched_getaffinity(0))[:4]
            require(affinity, 'CPU affinity')
            os.sched_setaffinity(0, affinity)
            for key in list(os.environ):
                if key.startswith(('ARK_', 'COOP_', 'STRONGHOLD_')):
                    del os.environ[key]
            root = safe(args.root)
            old_dir, new_dir, ref_dir, receipt_path, out = [safe(root / p) for p in (
                args.old_input, args.new_input, args.old_reference, args.source_acceptance_receipt, args.out)]
            disjoint(out, [CODE, old_dir, new_dir, ref_dir, receipt_path,
                           root / 'game', root / 'Stronghold-Protocol', root / 'experiments/bc-expansion-v1'])
            header(bound_json(new_dir / 'manifest.json', args.manifest_sha256)[0])
            source = source_identity()
            collector_source = S.source_identity()
            game_path, game = game_identity(root)
            parent = parent_path(root)
            accepted, receipt_raw = receipt(receipt_path, args.receipt_sha256, args.manifest_sha256, collector_source, game)
            evidence = Evidence(out, args.output_mib*MIB-4*MIB)
            evidence.put('source-acceptance-receipt.json', receipt_raw)
            evidence.json('source-before.json', dict(source=source, collector=collector_source, game=game))
            names = ['old6Reference', 'newOnly', 'combined'] if args.arms == 'all' else [args.arms]
            report.update(source=source, game=game, parentSHA256=PARENT_SHA, newManifestSHA256=args.manifest_sha256,
                          oldManifestSHA256=OLD_SHA, referenceCheckpointSHA256=REFERENCE_SHA,
                          sourceAcceptanceReceiptSHA256=args.receipt_sha256, requestedArms=names)
        with budgets.phase('audit') as deadline:
            samples, metadata = admit_all(old_dir, new_dir, args.manifest_sha256, ref_dir, game,
                                         collector_source, evidence, deadline, names)
        with budgets.phase('work'):
            labels = arm_labels(metadata)
            report['admission'] = {name: dict(labels=len(samples[name]),
                games=len({(r['corpus'], r['seed'], r['pool']) for r in labels[name]}),
                episodeGroups=len({r['episodeGroup'] for r in labels[name]}),
                sampleOrderSHA256=C.digest(C.encoded(labels[name]))) for name in names}
            evidence.json('admission-inventories.json.gz', dict(old=metadata['oldInventory'],
                new=metadata['new']['fullInventory'], reference=metadata['referenceInventory']), compress=True)
            if args.command == 'train':
                from train_scale import train
                L = C.learner(CODE)
                provenance = dict(sourceSHA256=C.digest(C.encoded(source)),
                    newManifestSHA256=args.manifest_sha256, oldManifestSHA256=OLD_SHA,
                    sourceAcceptanceReceiptSHA256=args.receipt_sha256,
                    admissionInventoriesSHA256=evidence.files['admission-inventories.json.gz']['sha256'])
                report.update(train(L, parent, ref_dir, samples, metadata, game, evidence, provenance, names))
        with budgets.phase('audit') as deadline:
            recheck(old_dir, new_dir, ref_dir, metadata, deadline)
        with budgets.phase('work'):
            exact(source_identity(), source, 'Final source drift')
            exact(S.source_identity(), collector_source, 'Final collector drift')
            require(game_identity(root) == (game_path, game), 'Final game486/static data drift')
            parent_path(root)
            require(file_id(receipt_path, limit=MIB)['sha256'] == args.receipt_sha256, 'Acceptance receipt drift')
            evidence.json('source-after.json', dict(source=source, collector=collector_source, game=game))
            evidence.verify()
            report.update(status='complete', finalChecks=dict(fullInputInventory=True, source=True,
                          game486=True, parent=True, receipt=True, publishedEvidence=True))
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}'[:2000])
    finally:
        budgets.close()
        report.update(seconds=time.monotonic()-start, phaseSeconds=budgets.used,
            finishedAt=datetime.now(timezone.utc).isoformat(), limits=dict(workSeconds=args.work_seconds,
            auditSeconds=args.audit_seconds, totalSeconds=args.work_seconds+args.audit_seconds,
            ramGiB=args.ram_gib, perProcessAddressSpaceGiB=args.ram_gib/2,
            outputMiB=args.output_mib, cpuMax=4))
        if evidence is not None:
            try:
                evidence.limit += 4*MIB  # Reserved final bounded report, including failures.
                report['evidence'] = dict(evidence.files)
                identity = evidence.json('train.json' if args.command == 'train' else 'admission.json', report)
                print(C.encoded(dict(status=report['status'], out=str(evidence.out), reportIdentity=identity)).decode(), end='')
            finally:
                evidence.close()
        else:
            print(C.encoded(report).decode(), end='')
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
