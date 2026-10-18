#!/usr/bin/env python3
"""Synthetic-only contracts. Default entry supervises one Torch child, <=20s/1GiB/5MB."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def supervise(out):
    # Reuse the read-only process-tree sampler; never start an engine or another agent.
    sys.path.insert(0, str(HERE.parent / 'interaction'))
    from guard import process_tree
    from common import json_bytes, write_json
    out = Path(out).resolve()
    if not out.is_relative_to(HERE) or out == HERE:
        raise ValueError('Test outputs must stay under tensor-bc/')
    out.mkdir(parents=True, exist_ok=False)
    start, last, peak, gap, count, known = time.monotonic(), time.monotonic(), 0, 0, 0, set()
    reason = None
    with (out / 'tests.log').open('xb') as log:
        child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--child', '--out', str(out)],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1',
                 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'})
        while True:
            now = time.monotonic()
            known, rss = process_tree(os.getpid(), known)
            peak = max(peak, sum(rss.values()))
            gap, last, count = max(gap, now - last), now, count + 1
            size = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
            if peak > 1024 ** 3:
                reason = 'process-tree RSS > 1 GiB'
            elif size > 5_000_000 - 8192:
                reason = 'total artifacts exceed reserved 5 MB budget'
            elif now - start >= 19:
                reason = '19s stop leaves cleanup margin within 20s'
            if reason or child.poll() is not None:
                break
            time.sleep(.01)
        remaining, _ = process_tree(os.getpid(), known)
        for pid in remaining - {os.getpid()}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        code = child.wait()
    report = {'exitCode': code, 'seconds': time.monotonic() - start,
              'peakSummedTreeRSSBytes': peak, 'rssLimitBytes': 1024 ** 3,
              'outputBytesBeforeResourceReport': size, 'outputLimitBytes': 5_000_000,
              'rssSamples': count, 'maxSampleGapSeconds': gap, 'terminationReason': reason,
              'measurement': 'Sampled /proc summed supervisor+all descendant RSS; not an instantaneous kernel bound',
              'passed': code == 0 and reason is None and time.monotonic() - start < 20}
    write_json(out / 'resources.json', report)
    print(json_bytes(report).decode(), end='')
    print((out / 'tests.log').read_text()[-6000:])
    return 0 if report['passed'] else 1


def contracts(out):
    import copy
    import io
    import json
    import unittest
    from contextlib import redirect_stdout
    from unittest.mock import patch
    import run
    import cache
    import check as original_fixtures
    import dataset
    import model
    import train
    from common import json_bytes, sha, write_json
    from model import L, torch

    out = Path(out)
    original_fixtures.OUT = out
    rows, manifest = original_fixtures.fixture()
    # Eight nontrivial samples: different widths, real false mask, tail batches.
    for i, row in enumerate(r for r in rows if r['type'] == 'sample'):
        width = [1, 4, 2, 3, 2, 1, 4, 3][i]
        row['features'] = {'global': [((i * 7 + j * 3) % 17 - 8) / 9 for j in range(90)],
                           'candidates': [[((i * 5 + k * 7 + j) % 13 - 6) / 7 for j in range(15)]
                                          for k in range(width)],
                           'mask': [k != 1 for k in range(width)]}
        row['action'] = width - 1 if width > 2 else 0
    data, meta = original_fixtures.put(rows, manifest, 'input')
    raw, admitted = dataset.load_dataset(data, meta, allow_legacy=True, synthetic=True)
    frozen, _ = cache.load_cache(data, meta, allow_legacy=True, synthetic=True)
    evidence = {'source': run.identity(), 'torchVersion': str(torch.__version__),
                'inputSHA256': {'dataset': sha(data), 'manifest': sha(meta)},
                'atol': 1e-7, 'rtol': 1e-5, 'bitwiseClaim': False,
                'scope': 'CPU synthetic empty-vocabulary, hidden=8, 8 samples, 3 epochs, batch=3; '
                         'no real sim, production data, GPU or server-speedup evidence',
                'maxAbsoluteDifference': 0., 'comparedTensorPairs': 0}

    def close(a, b):
        torch.testing.assert_close(a, b, atol=1e-7, rtol=1e-5)
        finite = torch.isfinite(a) & torch.isfinite(b)
        if finite.any() and a.dtype != torch.bool:
            evidence['maxAbsoluteDifference'] = max(evidence['maxAbsoluteDifference'],
                                                    (a[finite] - b[finite]).abs().max().item())
        evidence['comparedTensorPairs'] += 1

    def traced_pair(cached):
        samples = frozen.samples if cached else raw
        indices = {id(s['features']): i for i, s in enumerate(samples)}
        traces, instances = {}, []
        real_factory, real_step = model.make_model, torch.optim.Adam.step

        def factory(arm, dims):
            instance = real_factory(arm, dims)
            instances.append(instance)
            steps = traces[arm] = []
            pending = {}
            instance.state.register_forward_pre_hook(lambda _, args: pending.update(global_=args[0].detach().clone()))
            layer = instance.actor if arm == 'concat-baseline' else instance.candidate

            def candidates(_, args):
                value = args[0][..., dims[2]:] if arm == 'concat-baseline' else args[0]
                pending['candidates'] = value.detach().clone()
            layer.register_forward_pre_hook(candidates)

            def forward(_, args, result):
                order = [indices[id(f)] for f in args[0]]
                labels = torch.tensor([samples[i]['action'] for i in order])
                distribution, value = result
                steps.append({**pending, 'order': order, 'width': distribution.logits.shape[1],
                    'mask': torch.isfinite(distribution.logits).detach().clone(),
                    'logprob': distribution.log_prob(labels).detach().clone(),
                    'logits': distribution.logits.detach().clone(), 'value': value.detach().clone(),
                    'loss': -distribution.log_prob(labels).mean().detach().clone(), 'grads': {}})
            instance.register_forward_hook(forward)
            for name, parameter in instance.named_parameters():
                parameter.register_hook(lambda grad, name=name: steps[-1]['grads'].update({name: grad.detach().clone()}))
            return instance

        def step(optimizer, *args, **kwargs):
            instance = instances[-1]
            record = traces[model.ARMS[len(instances) - 1]][-1]
            record['clipped'] = {n: p.grad.detach().clone() for n, p in instance.named_parameters() if p.grad is not None}
            result = real_step(optimizer, *args, **kwargs)
            record['parameters'] = {n: p.detach().clone() for n, p in instance.named_parameters()}
            return result

        dest = run.output_dir(out / ('cached-pair' if cached else 'raw-pair'))
        with patch.object(model, 'make_model', factory), patch.object(train, 'make_model', factory), \
             patch.object(torch.optim.Adam, 'step', step):
            if cached:
                with frozen.training_models():
                    report = train.train_pair(samples, admitted, dest, hidden=8, epochs=3, batch_size=3,
                                              init_seed=101, order_seed=107, synthetic=True)
            else:
                report = train.train_pair(samples, admitted, dest, hidden=8, epochs=3, batch_size=3,
                                          init_seed=101, order_seed=107, synthetic=True)
        return traces, report, dest

    class Contracts(unittest.TestCase):
        def test_01_independent_numerics(self):
            reference, reference_report, _ = traced_pair(False)
            actual, actual_report, _ = traced_pair(True)
            self.assertEqual(reference_report['epochOrderSHA256'], actual_report['epochOrderSHA256'])
            for r, a in zip(reference_report['arms'], actual_report['arms']):
                self.assertEqual(r['initialParameterSHA256'], a['initialParameterSHA256'])
                self.assertEqual(a['metrics']['updates'], 9)
            for arm in model.ARMS:
                self.assertEqual(len(actual[arm]), 9)
                for expected, got in zip(reference[arm], actual[arm]):
                    self.assertEqual(expected['order'], got['order'])
                    self.assertEqual(expected['width'], got['width'])
                    self.assertEqual(got['width'], max(len(raw[i]['features']['mask']) for i in got['order']))
                    for key in ('global_', 'candidates', 'mask', 'logprob', 'logits', 'value', 'loss'):
                        close(expected[key], got[key])
                    for key in ('grads', 'clipped', 'parameters'):
                        self.assertEqual(expected[key].keys(), got[key].keys())
                        for name in expected[key]:
                            close(expected[key][name], got[key][name])
                    self.assertFalse(any(n.startswith('critic.') for n in got['grads']))
            evidence['updatesPerArm'] = 9
            evidence['arms'] = list(model.ARMS)
            evidence['batchWidths'] = {a: [b['width'] for b in actual[a]] for a in model.ARMS}

        def test_02_ownership_fallback_restore(self):
            samples = copy.deepcopy(raw[:3])
            samples.append({'features': samples[1]['features'], 'action': samples[1]['action']})
            duplicated = cache._TensorCache(samples, [90, 15])
            self.assertIsNot(duplicated.samples[1]['features'], duplicated.samples[3]['features'])
            factory, loader, admission = train.make_model, model.load_checkpoint, dataset.load_dataset
            external = factory('interaction', [90, 15, 8])
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                with duplicated.training_models():
                    instance = train.make_model('interaction', [90, 15, 8])
                    self.assertNotIn('forward', external.__dict__)
                    handles = [s['features'] for s in duplicated.samples]
                    before = instance(handles)
                    with patch.object(model, 'validate_features', side_effect=AssertionError('validation called')):
                        instance(handles)  # The hot path truly avoids Python feature validation.
                        with self.assertRaisesRegex(AssertionError, 'validation called'):
                            instance([raw[0]['features']])
                    reloaded, _ = model.load_checkpoint(out / 'raw-pair' / 'interaction.pt')
                    self.assertNotIn('forward', reloaded.__dict__)
                    reloaded([raw[0]['features']])
                    with self.assertRaises(ValueError):
                        reloaded(handles)
                    close(before[0].logits[1], before[0].logits[3])
                    # Raw corruption cannot change the owned snapshot or become a cache hit.
                    samples[1]['features']['global'][0] = float('nan')
                    close(before[0].logits, instance(handles)[0].logits)
                    with self.assertRaises(ValueError):
                        instance([samples[1]['features']])
                    with self.assertRaises(TypeError):
                        handles[0]['mask'][0] = False
                    with self.assertRaises(TypeError):
                        duplicated.samples[0]['action'] = 99
                    with self.assertRaises(ValueError):
                        instance([handles[0], raw[0]['features']])
                    with self.assertRaises(ValueError):
                        instance([{'mask': (True,)}])
                    close(instance([raw[0]['features']])[0].logits,
                          model.Interaction.forward(instance, [raw[0]['features']])[0].logits)
                    instance.eval()
                    with self.assertRaises(ValueError):
                        instance(handles)
                    close(instance([raw[0]['features']])[0].logits,
                          model.Interaction.forward(instance, [raw[0]['features']])[0].logits)
                    instance.train()
                    with torch.no_grad(), self.assertRaises(ValueError):
                        instance(handles)
                    raise RuntimeError('injected')
            self.assertIs(train.make_model, factory)
            self.assertIs(model.load_checkpoint, loader)
            self.assertIs(dataset.load_dataset, admission)
            self.assertNotIn('forward', instance.__dict__)
            with self.assertRaises(ValueError):
                instance(handles)
            with duplicated.training_models():
                train.make_model('concat-baseline', [90, 15, 8])([handles[0], handles[0]])

        def test_03_whole_file_admission(self):
            bad_cases = []
            changed = copy.deepcopy(rows); changed[1]['features']['global'].pop(); bad_cases.append(changed)
            changed = copy.deepcopy(rows); changed[1]['features']['mask'] = [False]; bad_cases.append(changed)
            changed = copy.deepcopy(rows); changed[1]['seed'] = 99; bad_cases.append(changed)
            bad_cases += [rows[:-1], rows + [rows[-1]], rows + [{'type': 'unexpected'}]]
            changed = copy.deepcopy(rows); changed[0]['schema']['version'] = 'bad'; bad_cases.append(changed)
            for i, bad in enumerate(bad_cases):
                paths = original_fixtures.put(bad, manifest, 'bad-' + str(i))
                with self.assertRaises(ValueError):
                    cache.load_cache(*paths, allow_legacy=True, synthetic=True)
            paths = original_fixtures.put(rows, manifest, 'eof')
            paths[0].write_bytes(paths[0].read_bytes().rstrip(b'\n'))
            with self.assertRaises(ValueError):
                cache.load_cache(*paths, allow_legacy=True, synthetic=True)
            with self.assertRaises(ValueError):
                cache.load_cache(data, meta, allow_legacy=True, synthetic=True,
                                 expected_schema={**manifest['schema'], 'ids': ['foreign']})
            with self.assertRaises(ValueError):
                cache.load_cache(data, meta, synthetic=True)  # Legacy remains opt-in.
            with self.assertRaises(ValueError):
                cache.load_cache(data, meta, allow_legacy=True)  # No production-size bypass.
            original = dataset.load_dataset
            paths = original_fixtures.put(rows, manifest, 'drifting-input')
            def drifting(*args, **kwargs):
                result = original(*args, **kwargs)
                paths[1].write_bytes(paths[1].read_bytes() + b' ')
                return result
            with patch.object(dataset, 'load_dataset', drifting), self.assertRaisesRegex(ValueError, 'Input changed'):
                cache.load_cache(*paths, allow_legacy=True, synthetic=True)

        def test_04_cli_metadata_loader_and_nooverwrite(self):
            dest = out / 'cli'
            argv = ['--tensor-cache', '--data', str(data), '--manifest', str(meta), '--out', str(dest),
                    '--synthetic-small', '--allow-legacy-metadata', '--hidden', '8', '--epochs', '3',
                    '--batch-size', '3', '--order-seed', '107']
            with redirect_stdout(io.StringIO()):
                run.main(argv)
            saved = json.loads((dest / 'train.json').read_text())
            self.assertEqual(saved['status'], 'complete')
            self.assertEqual(saved['source']['sourceSHA256'], run.identity()['sourceSHA256'])
            self.assertIn('tensor-bc/cache.py', saved['source']['tensorcacheFiles'])
            self.assertIn('tensor-bc/run.py', saved['source']['tensorcacheFiles'])
            self.assertEqual(saved['source']['tensorCache']['inputSHA256']['dataset'], sha(data))
            for arm in model.ARMS:
                loaded, ckpt = model.load_checkpoint(dest / (arm + '.pt'))
                reference, _ = model.load_checkpoint(out / 'raw-pair' / (arm + '.pt'))
                close(L.vector(loaded), L.vector(reference))
                self.assertEqual(ckpt['format'], model.FORMAT)
                self.assertEqual(ckpt['metadata']['source'], saved['source'])
                self.assertNotIn('forward', loaded.__dict__)
                with self.assertRaises(ValueError):
                    loaded([{'global': [], 'candidates': [], 'mask': []}])
                close(loaded([raw[2]['features']])[0].logits, reference([raw[2]['features']])[0].logits)
            before = {p.name: sha(p) for p in dest.iterdir()}
            with self.assertRaises(FileExistsError):
                run.main(argv)
            self.assertEqual(before, {p.name: sha(p) for p in dest.iterdir()})
            with self.assertRaisesRegex(ValueError, 'opt-in'):
                run.main(argv[1:])
            with self.assertRaisesRegex(ValueError, 'explicit --game'):
                run.main([a for a in argv if a != '--synthetic-small'])
            with self.assertRaises(ValueError):
                run.output_dir(HERE.parent / 'interaction' / 'forbidden')
            # Production CLI uses actual pinned game contract before data admission.
            with patch.object(run, 'game_schema', side_effect=ValueError('pin sentinel')) as pin, \
                 self.assertRaisesRegex(ValueError, 'pin sentinel'):
                run.main([a for a in argv if a != '--synthetic-small'] + ['--game', '/actual/game'])
            pin.assert_called_once_with('/actual/game')

        def test_05_source_drift_and_exception_cleanup(self):
            factory, saver, source, loader = train.make_model, train.save_checkpoint, train.source_identity, model.load_checkpoint
            argv = ['--tensor-cache', '--data', str(data), '--manifest', str(meta), '--synthetic-small',
                    '--allow-legacy-metadata', '--hidden', '8', '--epochs', '1', '--out']
            current = run.file_hashes()
            drift = {**current, 'tensor-bc/cache.py': '0' * 64}
            with patch.object(run, 'file_hashes', return_value=drift), self.assertRaisesRegex(ValueError, 'Source drift'):
                run.main(argv + [str(out / 'pre-drift')])
            self.assertFalse((out / 'pre-drift').exists())
            # Drift immediately before save, during save, or after the second save.
            for mode in ('before-save', 'during-save', 'after-pair', 'exception'):
                dest, saved_count, changed = out / mode, [0], [False]
                original_bc = L.bc_update
                def bc(*args, **kwargs):
                    result = original_bc(*args, **kwargs)
                    if mode == 'before-save':
                        changed[0] = True
                    if mode == 'exception':
                        raise RuntimeError('injected BC failure')
                    return result
                def save(*args, **kwargs):
                    saver(*args, **kwargs)
                    saved_count[0] += 1
                    if mode == 'during-save':
                        changed[0] = True
                original_pair = train.train_pair
                def pair(*args, **kwargs):
                    result = original_pair(*args, **kwargs)
                    if mode == 'after-pair':
                        changed[0] = True
                    return result
                with patch.object(run, 'file_hashes', side_effect=lambda: drift if changed[0] else current), \
                     patch.object(train, 'save_checkpoint', save), patch.object(L, 'bc_update', bc), \
                     patch.object(train, 'train_pair', pair), self.assertRaises((ValueError, RuntimeError)):
                    run.main(argv + [str(dest)])
                self.assertEqual(list(dest.glob('*.pt')), [])
                self.assertEqual(json.loads((dest / 'train.json').read_text())['status'], 'failed')
                self.assertIs(train.make_model, factory)
                self.assertIs(train.save_checkpoint, saver)
                self.assertIs(train.source_identity, source)
                self.assertIs(model.load_checkpoint, loader)

        def test_06_local_timing(self):
            # Same raw features repeated to batch32; timer excludes admission/cache creation.
            times = {}
            for arm in model.ARMS:
                instance = model.make_model(arm, [90, 15, 8])
                raw_batch = [s['features'] for s in raw] * 4
                for _ in range(2):
                    instance(raw_batch)
                start = time.perf_counter()
                for _ in range(20):
                    instance(raw_batch)
                plain = time.perf_counter() - start
                with frozen.training_models():
                    fast = train.make_model(arm, [90, 15, 8])
                    fast.load_state_dict(instance.state_dict())
                    batch = [s['features'] for s in frozen.samples] * 4
                    for _ in range(2):
                        fast(batch)
                    start = time.perf_counter()
                    for _ in range(20):
                        fast(batch)
                    cached = time.perf_counter() - start
                times[arm] = {'originalSeconds': plain, 'cacheSeconds': cached, 'forwards': 20, 'batch': 32}
            evidence['localTimingOnly'] = times

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence['passed'] = result.wasSuccessful()
    evidence['testsRun'] = result.testsRun
    write_json(out / 'numerics.json', evidence)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    raise SystemExit(contracts(args.out) if args.child else supervise(args.out))
