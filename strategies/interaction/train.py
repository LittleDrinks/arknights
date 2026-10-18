#!/usr/bin/env python3
"""Paired BC ablation only. Production data stays on the server; no collection/PPO."""
import argparse
import hashlib
import math
import time

from common import (DEFAULT_GAME, game_schema, json_bytes, output_dir, require,
                    schema_dimensions, sha, source_identity, write_json)
from dataset import load_dataset
from model import ARMS, L, make_model, parameter_counts, save_checkpoint, torch


def train_pair(samples, admitted, out, *, hidden=128, epochs=24, lr=.0005, batch_size=64,
               init_seed=101, order_seed=101, synthetic=False):
    require(type(epochs) is int and epochs > 0 and type(batch_size) is int and batch_size > 0 and
            math.isfinite(lr) and lr > 0, 'Invalid BC hyperparameters')
    dims = schema_dimensions(admitted['schema']) + [hidden]
    config = {'hidden': hidden, 'epochs': epochs, 'lr': lr, 'batchSize': batch_size,
              'initSeed': init_seed, 'orderSeed': order_seed, 'optimizer': 'Adam', 'maxGradNorm': 0.5}
    generator = torch.Generator().manual_seed(order_seed)
    order_hash = hashlib.sha256()
    for _ in range(epochs):
        order_hash.update(json_bytes(torch.randperm(len(samples), generator=generator).tolist()))
    report = {'kind': 'paired-BC-architecture-ablation', 'status': 'running', 'syntheticOnly': synthetic,
              'config': config, 'dataset': admitted, 'source': source_identity(), 'torchVersion': str(torch.__version__),
              'epochOrderSHA256': order_hash.hexdigest(), 'arms': [],
              'hypothesis': 'Explicit state-candidate products may help; no proven expressivity deficit or improvement.',
              'initialization': 'PyTorch Linear defaults, same seed; shared state/critic copied bit-for-bit. '
                                'Actor topology, parameter count and RNG consumption differ; not parameter-matched.'}
    write_json(out / 'train.json', report)
    shared = None
    try:
        for arm in ARMS:
            torch.manual_seed(init_seed)
            model = make_model(arm, dims)
            if shared is None:
                shared = {key: value.clone() for key, value in model.state_dict().items()
                          if key.startswith(('state.', 'critic.'))}
            else:
                model.load_state_dict({**model.state_dict(), **shared}, strict=True)
            initial = L.digest(model)
            # bc_update only consumes randperm RNG: no dropout/sampling in either forward.
            # Reset AFTER architecture initialization to make every epoch order identical.
            torch.manual_seed(order_seed)
            start = time.monotonic()
            metrics, _ = L.bc_update(model, samples, epochs, lr, batch_size)
            record = {'architecture': arm, 'parameterCounts': parameter_counts(model),
                      'initialParameterSHA256': initial, 'metrics': metrics,
                      'epochOrderSHA256': order_hash.hexdigest(), 'seconds': time.monotonic() - start}
            checkpoint = out / (arm + '.pt')
            save_checkpoint(checkpoint, model, arm, admitted['schema'],
                            {'config': config, 'dataset': admitted, 'source': report['source'],
                             'syntheticOnly': synthetic, 'initialization': report['initialization'], **record})
            record['checkpointSHA256'] = sha(checkpoint)
            record['checkpoint'] = checkpoint.name
            report['arms'].append(record)
            write_json(out / 'train.json', report)
        report['status'] = 'complete'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(out / 'train.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--game', default=str(DEFAULT_GAME))
    parser.add_argument('--allow-legacy-metadata', action='store_true')
    parser.add_argument('--synthetic-small', action='store_true', help='Empty-vocabulary, <=128-label contract data ONLY')
    parser.add_argument('--epochs', type=int, default=24)
    parser.add_argument('--hidden', type=int, default=128)
    parser.add_argument('--lr', type=float, default=.0005)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--init-seed', type=int, default=101)
    parser.add_argument('--order-seed', type=int, default=101)
    args = parser.parse_args()
    samples, admitted = load_dataset(args.data, args.manifest, allow_legacy=args.allow_legacy_metadata,
                                    synthetic=args.synthetic_small,
                                    expected_schema=None if args.synthetic_small else game_schema(args.game))
    if args.synthetic_small:
        require(args.epochs <= 200 and args.hidden <= 32, 'Small synthetic training only')
    out = output_dir(args.out)
    report = train_pair(samples, admitted, out, hidden=args.hidden, epochs=args.epochs, lr=args.lr,
                        batch_size=args.batch_size, init_seed=args.init_seed, order_seed=args.order_seed,
                        synthetic=args.synthetic_small)
    print(json_bytes({'status': report['status'], 'out': str(out), 'arms': report['arms']}).decode(), end='')


if __name__ == '__main__':
    main()
