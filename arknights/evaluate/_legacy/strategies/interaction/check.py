#!/usr/bin/env python3
"""Small synthetic contracts; optional ONE bounded, zero-weight real smoke episode."""
import argparse
import copy
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from common import (DEFAULT_GAME, HERE, RULES, game_schema, json_bytes, output_dir, schema_dimensions,
                    sha, strict_json, write_json)
from dataset import load_dataset
from model import ARMS, L, load_checkpoint, make_model, save_checkpoint, torch
from eval import evaluate
import eval as eval_cli
import train as train_cli

SCHEMA = {'version': 'shop-v1', 'ids': [], 'bands': [], 'bonds': []}
OUT = None


def feature(sign=1):
    g = [0.] * 90
    g[0] = sign
    positive, negative = [0.] * 15, [0.] * 15
    positive[0], negative[0] = 1., -1.
    return {'global': g, 'candidates': [positive, negative], 'mask': [True, True]}


def fixture():
    header = {'type': 'metadata', 'schema': SCHEMA, 'rules': RULES, 'teacher': 'synthetic-contract'}
    rows = [header]
    episodes = []
    # Interleave two episodes, as in the original all.jsonl expansion.
    for _ in range(4):
        for seed, sign in ((101, 1), (102, -1)):
            rows.append({'type': 'sample', 'seed': seed, 'difficulty': 'NORMAL', 'victory': False,
                         'round': 1, 'features': feature(sign), 'action': int(sign < 0)})
    for seed in (101, 102):
        rows.append({'type': 'episode', 'seed': seed, 'difficulty': 'NORMAL', 'mode': 'solo', 'budget': 128,
                     'victory': False, 'rounds': 1, 'steps': 4, 'engineErrors': 0, 'actionErrors': 0,
                     'controllerErrors': 0, 'budgetForcedReady': 0, 'controllerBlockedSales': 0, 'rejectedLabels': 0})
        episodes.append({'seed': seed, 'difficulty': 'NORMAL', 'mode': 'solo', 'budget': 128,
                         'samples': 4, 'victory': False, 'split': 'train'})
    manifest = {'format': 'interaction-dataset/v1', 'recordFormat': 'legacy-demo', 'split': 'train',
                'schema': SCHEMA, 'rules': RULES, 'teacher': 'synthetic-contract', 'sourceSha256': {'fixture': '0' * 64},
                'episodeCount': 2, 'sampleCount': 8, 'episodes': episodes,
                'provenance': {'collector': 'synthetic-contract', 'reviewedBy': 'contract-test',
                               'trainingSplitEvidence': 'generated solely for tests',
                               'terminalAdmission': 'synthetic legacy metadata, not real engine evidence'}}
    return rows, manifest


def put(rows, manifest, stem='fixture'):
    data, meta = OUT / (stem + '.jsonl'), OUT / (stem + '.manifest.json')
    data.write_bytes(b''.join(json_bytes(row) for row in rows))
    manifest = copy.deepcopy(manifest)
    manifest['datasetSHA256'] = sha(data)
    write_json(meta, manifest)
    return data, meta


class Contracts(unittest.TestCase):
    def test_baseline_is_original_and_masks(self):
        torch.manual_seed(5)
        original = L.ActorCritic(90, 15, 8)
        torch.manual_seed(5)
        baseline = make_model('concat-baseline', [90, 15, 8])
        self.assertTrue(torch.equal(L.vector(original), L.vector(baseline)))
        f = feature()
        short = copy.deepcopy(f)
        short['candidates'], short['mask'] = short['candidates'][:1], [True]
        masked = copy.deepcopy(f)
        masked['mask'] = [True, False]
        for arm in ARMS:
            model = make_model(arm, [90, 15, 8])
            d, _ = model([f, short, masked])
            self.assertEqual(d.probs[1, 1].item(), 0)
            self.assertEqual(d.probs[2, 1].item(), 0)
            for _ in range(8):
                self.assertEqual(model.act(masked)[0], 0)
            all_masked = {**f, 'mask': [False, False]}
            with self.assertRaises(ValueError):
                model([all_masked])
            with self.assertRaises(ValueError):
                L.bc_update(model, [{'features': masked, 'action': 1}], epochs=1)
            metrics, _ = L.bc_update(model, [{'features': x, 'action': 0} for x in (f, short, masked)],
                                     epochs=1, batch_size=3)
            self.assertGreater(metrics['parameterDeltaL2'], 0)
            trained, _ = model([f, short, masked])
            self.assertEqual(trained.probs[1, 1].item(), 0)
            self.assertEqual(trained.probs[2, 1].item(), 0)
        self.assertTrue(torch.equal(original([f, short])[0].logits, baseline([f, short])[0].logits))

    def test_dataset_admission_and_truncated_tails(self):
        rows, manifest = fixture()
        paths = put(rows, manifest)
        samples, admitted = load_dataset(*paths, allow_legacy=True, synthetic=True)
        self.assertEqual(len(samples), 8)
        self.assertTrue(all(set(s) == {'features', 'action'} for s in samples))
        self.assertIn('inherited', admitted['terminalTrust'])
        with self.assertRaises(ValueError):
            load_dataset(*paths, synthetic=True)
        cases = []
        bad = copy.deepcopy(rows); bad[1]['features']['mask'][0] = False; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[1]['features']['seed'] = 101; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[1]['terminal'] = {}; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[1]['action'] = True; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[1]['features']['candidates'][0].pop(); cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[1]['seed'] = 999; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[-1]['controllerErrors'] = 1; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[-1]['mode'] = 'duo'; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[1]['split'] = 'test'; cases.append((bad, manifest))
        bad = copy.deepcopy(rows); bad[0]['rules'] = 'bad'; cases.append((bad, manifest))
        bad_manifest = copy.deepcopy(manifest); bad_manifest['episodes'][0]['split'] = 'validation'
        cases.extend([(rows, bad_manifest), (rows[:-1], manifest), (rows[:1] + rows[2:], manifest),
                      (rows + [rows[-1]], manifest), (rows + [{'type': 'unexpected'}], manifest)])
        for i, (bad_rows, bad_manifest) in enumerate(cases):
            with self.subTest(case=i), self.assertRaises((ValueError, KeyError)):
                load_dataset(*put(bad_rows, bad_manifest, 'bad'), allow_legacy=True, synthetic=True)
        data, meta = put(rows, manifest, 'tail')
        data.write_bytes(data.read_bytes()[:-2])
        with self.assertRaises(ValueError):
            load_dataset(data, meta, allow_legacy=True, synthetic=True)
        with self.assertRaises(ValueError):
            strict_json('{"a":1,"a":2}')
        with self.assertRaises(ValueError):
            strict_json('{"a":NaN}')

    def test_full_terminal_admission(self):
        rows, manifest = fixture()
        manifest['recordFormat'] = 'full-terminal-v1'
        for i in (-2, -1):
            old = rows[i]
            terminal = {k: v for k, v in old.items() if k not in ('type', 'rejectedLabels')}
            terminal.update(done=True, schema=SCHEMA, rules=RULES,
                            episode={k: old[k] for k in ('seed', 'difficulty', 'mode', 'budget')},
                            summary={'victory': False, 'roundsPassed': 1, 'seed': old['seed'], 'difficulty': 'NORMAL',
                                     'reason': 'synthetic', 'errors': 0,
                                     'players': [{'playerId': 'learner', 'victory': False, 'roundsPassed': 1}]},
                            observation={'own': {}, 'public': {}, 'history': {}}, reward=-1,
                            controller='upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1',
                            candidates=[], features={'global': [0.] * 90, 'candidates': [], 'mask': []})
            rows[i] = {'type': 'episode', 'seed': old['seed'], 'difficulty': 'NORMAL', 'rejectedLabels': 0,
                       'terminal': terminal}
        load_dataset(*put(rows, manifest, 'full'), synthetic=True)
        for key, value in [('done', False), ('summary', None), ('engineErrors', 1), ('candidates', [1])]:
            bad = copy.deepcopy(rows); bad[-1]['terminal'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_dataset(*put(bad, manifest, 'full-bad'), synthetic=True)

    def test_paired_training_cli_reload_and_state_preference(self):
        rows, manifest = fixture()
        data, meta = put(rows, manifest, 'train-input')
        out = OUT / 'paired'
        argv = ['train.py', '--data', str(data), '--manifest', str(meta), '--allow-legacy-metadata',
                '--synthetic-small', '--epochs', '80', '--hidden', '8', '--lr', '.03', '--batch-size', '8',
                '--out', str(out)]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            train_cli.main()
        report = strict_json((out / 'train.json').read_text())
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['arms'][0]['epochOrderSHA256'], report['arms'][1]['epochOrderSHA256'])
        # Independently audit ACTUAL batch order by recording forward inputs through bc_update.
        orders = []
        for arm in ARMS:
            torch.manual_seed(101)
            probe = make_model(arm, [90, 15, 8])
            samples = [{'features': feature(i + 1), 'action': 0} for i in range(7)]
            seen = []
            handle = probe.register_forward_pre_hook(lambda _, args: seen.extend(f['global'][0] for f in args[0]))
            torch.manual_seed(101)
            L.bc_update(probe, samples, epochs=2, batch_size=3)
            handle.remove()
            orders.append(seen)
        self.assertEqual(orders[0], orders[1])
        generator = torch.Generator().manual_seed(101)
        self.assertEqual(orders[0], [i + 1 for _ in range(2) for i in torch.randperm(7, generator=generator).tolist()])
        for arm in ARMS:
            path = out / (arm + '.pt')
            model, ckpt = load_checkpoint(path)
            probs = model([feature(1), feature(-1)])[0].probs
            self.assertGreater(probs[0, 0].item(), .9)
            self.assertLess(probs[1, 0].item(), .1)  # SAME candidates, different state -> opposite preference.
            again = OUT / (arm + '-reload.pt')
            save_checkpoint(again, model, arm, SCHEMA, ckpt['metadata'])
            reloaded, _ = load_checkpoint(again)
            self.assertTrue(torch.equal(probs, reloaded([feature(1), feature(-1)])[0].probs))
            with self.assertRaises(KeyError):
                L.load_checkpoint(path)
            broken = copy.deepcopy(ckpt); broken['architecture']['name'] = 'unknown'
            torch.save(broken, OUT / 'bad-checkpoint.pt')
            with self.assertRaises(ValueError):
                load_checkpoint(OUT / 'bad-checkpoint.pt')
            broken = copy.deepcopy(ckpt); next(iter(broken['policyState'].values())).fill_(float('nan'))
            torch.save(broken, OUT / 'bad-checkpoint.pt')
            with self.assertRaises(ValueError):
                load_checkpoint(OUT / 'bad-checkpoint.pt')
        self.assertNotEqual(report['arms'][0]['parameterCounts']['total'], report['arms'][1]['parameterCounts']['total'])
        write_json(OUT / 'synthetic-metrics.json', report['arms'])

    def test_planned_failure_denominator_cli(self):
        out = OUT / 'failed-eval'
        argv = ['eval.py', '--checkpoint', str(OUT / 'missing.pt'), '--out', str(out)]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eval_cli.main(), 2)
        report = strict_json((out / 'eval.json').read_text())
        self.assertEqual(report['planned'], 32)
        self.assertEqual(len(report['episodes']), 32)
        self.assertTrue(all(g['planned'] == 16 and g['failed'] == 16 and g['winsOverPlanned'] == 0
                            for g in report['groups']))


def real_smoke():
    # Zero weights are deliberately small synthetic weights, NOT a 16k-trained policy.
    schema = game_schema(DEFAULT_GAME)
    model = make_model('interaction', schema_dimensions(schema) + [4])
    with torch.no_grad():
        for p in model.parameters():
            p.zero_()
    checkpoint = OUT / 'zero-weight-real-schema.pt'
    save_checkpoint(checkpoint, model, 'interaction', schema,
                    {'syntheticOnly': True, 'note': 'All-zero weights, greedy ties select first legal candidate; no BC data.'})
    report = evaluate([checkpoint], output_dir(OUT / 'real-eval'), seeds=[1], difficulties=('NORMAL',),
                      wall_seconds=6, max_output_bytes=2_500_000)
    print(json_bytes({'realSmoke': report['status'], 'seconds': report['seconds'], 'groups': report['groups']}).decode(), end='')
    # Incomplete is evidence of a budget limit, never a fabricated contract pass.
    return report['status'] == 'complete'


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--real-only', action='store_true')
    args = parser.parse_args()
    OUT = output_dir(args.out)
    if args.real_only:
        return 0 if real_smoke() else 2
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(OUT / 'contracts.json', {'tests': result.testsRun, 'failures': len(result.failures),
                                       'errors': len(result.errors), 'passed': result.wasSuccessful(),
                                       'realSimulationRun': False})
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
