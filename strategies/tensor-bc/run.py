#!/usr/bin/env python3
"""Explicit opt-in tensor BC; run in a NEW process with a NEW output directory."""
import argparse
import hashlib
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = 'ce9c4c6f624b1093872b3192db95a122aafaf0e0'
sys.path.insert(0, str(ROOT / 'interaction'))


def file_hashes():
    paths = (sorted((ROOT / 'interaction').glob('*.py')) + sorted((ROOT / 'rl').glob('*.mjs')) +
             [ROOT / 'rl/learner.py'] + sorted(HERE.glob('*.py')))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


# Detect edits after source was read/imported, not just edits during training.
IMPORTED_FILES = file_hashes()
from common import game_schema, json_bytes, require, source_identity, write_json
import cache as tensor_cache
import train


def output_dir(path):
    path = Path(path).resolve()
    require(path.is_relative_to(HERE) and path != HERE, 'Output must be under this project tensor-bc/')
    path.mkdir(parents=True, exist_ok=False)
    return path


def identity():
    original = source_identity()
    files = file_hashes()
    require(files == IMPORTED_FILES, 'Source drift since import')
    require(all(files[k] == v for k, v in original['files'].items()), 'Source changed during hashing')
    cached = {k: v for k, v in files.items() if k.startswith('tensor-bc/')}
    return {'baseCommit': BASE, 'headCommit': original['headCommit'], 'files': files,
            'sourceSHA256': hashlib.sha256(json_bytes(files)).hexdigest(),
            'originalSource': original,
            'tensorcacheSourceSHA256': hashlib.sha256(json_bytes(cached)).hexdigest(),
            'tensorcacheFiles': cached}


def run(args):
    require(args.tensor_cache, 'Explicit --tensor-cache opt-in required')
    source = identity()
    if args.synthetic_small:
        require(0 < args.epochs <= 200 and 0 < args.hidden <= 32, 'Small synthetic training only')
        schema = None
    else:
        require(args.game is not None, 'Production requires explicit --game (actual pinned game root)')
        schema = game_schema(args.game)
    start = time.monotonic()
    cache, admitted = tensor_cache.load_cache(args.data, args.manifest,
        allow_legacy=args.allow_legacy_metadata, synthetic=args.synthetic_small, expected_schema=schema)
    cache_seconds = time.monotonic() - start
    require(identity() == source, 'Source drift during admission')
    out = output_dir(args.out)
    metadata = {'enabled': True, 'ownership': 'private fixed tensors; immutable strongly owned handles; fresh batch copies',
                'inputSHA256': {'dataset': admitted['datasetSHA256'], 'manifest': admitted['manifestSHA256']},
                'algorithmIdentity': 'Unmodified interaction.train.train_pair and rl.learner.bc_update; '
                    'same init/shared state+critic, shuffle, Adam, lr, clip=0.5; original forward equations',
                'numericalValidation': {'test': 'tensor-bc/tests.py', 'atol': 1e-7, 'rtol': 1e-5,
                    'scope': 'Independent CPU synthetic comparison, both arms, each batch and 9 Adam updates; '
                             'not a production-data or cross-device proof. See source-matched test evidence.',
                    'bitwiseClaim': False, 'performedInThisTrainingRun': False},
                'torchVersion': str(tensor_cache.torch.__version__),
                'admissionAndCacheSeconds': cache_seconds, 'tensorBytes': cache.tensor_bytes,
                'timingScope': 'Current machine only; server measurement required for server speedup claim'}
    original_save, original_source = train.save_checkpoint, train.source_identity
    checkpoints = []

    def checked_source():
        require(identity() == source, 'Source drift')
        return {**source, 'tensorCache': metadata}

    def checked_save(path, model, arm, schema, info):
        checked_source()
        # Exclusive creation; the original dedicated serializer remains unchanged.
        with Path(path).open('xb') as stream:
            checkpoints.append(Path(path))
            original_save(stream, model, arm, schema, info)
        checked_source()  # Never retain a checkpoint if code changed during save.

    train.save_checkpoint, train.source_identity = checked_save, checked_source
    try:
        with cache.training_models():
            report = train.train_pair(cache.samples, admitted, out, hidden=args.hidden, epochs=args.epochs,
                lr=args.lr, batch_size=args.batch_size, init_seed=args.init_seed, order_seed=args.order_seed,
                synthetic=args.synthetic_small)
        checked_source()
        return report
    except BaseException as error:
        for path in checkpoints:
            path.unlink(missing_ok=True)
        write_json(out / 'train.json', {'status': 'failed', 'error': str(error), 'source': source,
                                      'tensorCache': metadata, 'checkpointsRemoved': True})
        raise
    finally:
        train.save_checkpoint, train.source_identity = original_save, original_source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tensor-cache', action='store_true', help='Required explicit opt-in')
    parser.add_argument('--data', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--game', help='Actual game root; required outside synthetic mode')
    parser.add_argument('--allow-legacy-metadata', action='store_true')
    parser.add_argument('--synthetic-small', action='store_true')
    parser.add_argument('--epochs', type=int, default=24)
    parser.add_argument('--hidden', type=int, default=128)
    parser.add_argument('--lr', type=float, default=.0005)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--init-seed', type=int, default=101)
    parser.add_argument('--order-seed', type=int, default=101)
    report = run(parser.parse_args(argv))
    print(json_bytes({'status': report['status'], 'arms': report['arms']}).decode(), end='')


if __name__ == '__main__':
    main()
