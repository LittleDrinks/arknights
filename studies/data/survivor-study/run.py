#!/usr/bin/env python3
"""Server entry point: train, then explicit dev-only eval. No automatic games after BC."""
import argparse
from pathlib import Path

from common import (code_root, game_identity, learner, new_output, relative, require, source_identity, sha, write)
from data import admit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    train = sub.add_parser('train')
    ev = sub.add_parser('eval')
    for p in (train, ev):
        p.add_argument('--root', required=True, type=Path)
        p.add_argument('--experiment-code', help='Code root relative to --root, or absolute; auto: root / root/experiment-code')
        p.add_argument('--parent', required=True, help='The original bc-expansion all.pt; never an arm checkpoint')
        p.add_argument('--out', required=True)
    train.add_argument('--input-dir', required=True)
    train.add_argument('--primary', required=True)
    ev.add_argument('--trained', required=True)
    ev.add_argument('--dev-only', action='store_true', required=True)
    ev.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    ev.add_argument('--timeout', type=int, default=300)
    ev.add_argument('--total-timeout', type=int, default=14400)
    ev.add_argument('--output-mib', type=int, default=32)
    args = parser.parse_args()
    root = args.root.resolve()
    code = code_root(root, args.experiment_code)
    parent, target = relative(root, args.parent), root / args.out
    # Check before resolving so dangling symlinks are not silently followed.
    require(not target.exists() and not target.is_symlink(), 'Output already exists')
    require(parent.name == 'all.pt', '--parent must be the designated bc-expansion all.pt')
    source = source_identity(code)
    game_path, game = game_identity(root, code)
    if args.command == 'train':
        input_dir, primary = relative(root, args.input_dir), relative(root, args.primary)
        admitted = admit(input_dir, primary, source=source, game=game)
        L = learner(code)
        from train import train as run_train
        out = new_output(target)
        report = run_train(L, parent, admitted, out, source, game)
        try:
            require(source_identity(code) == source, 'Source drift during training')
            require(game_identity(root, code)[1] == game, 'Game source/data drift during training')
            for name, identity in admitted['inputSHA'].items():
                require(sha(primary if name == 'primary' else input_dir / name) == identity['sha256'], 'Input drift during training')
        except BaseException as exc:
            report.update(status='failed', error=str(exc))
            write(out / 'train.json', report)
            raise
    else:
        require(1 <= args.timeout <= 600 and 1 <= args.total_timeout <= 21600 and 1 <= args.output_mib <= 64,
                'Eval bounds: timeout <=600s, run <=21600s, per-game output <=64 MiB')
        L = learner(code)
        from evaluate import run_evaluation
        out = new_output(target)
        report = run_evaluation(L, relative(root, args.trained), parent, code, game_path, game, out, source,
                          workers=args.workers, timeout=args.timeout, total_timeout=args.total_timeout,
                          byte_limit=args.output_mib * 1024 * 1024)
    print(f"{args.command}: {report['status']} -> {out}")
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
