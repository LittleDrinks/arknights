"""Tiny synthetic tests only. No optimizer step, environment process, or real demo."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from rl.learner import ActorCritic, load_checkpoint, validate_checkpoint
import torch
from warm_start import warm_start
from recapture import verify_recapture, same, source_bindings, unpack, capture, main, sha, RULES, TEAMS


class GoldTests(unittest.TestCase):
    def test_warm_start(self):
        torch.manual_seed(41)
        schema = dict(version='shop-v1', ids=['a', 'b', 'item'], bonds=[], bands=[])
        g, c, hidden = 129, 18, 4
        parent = ActorCritic(g, c, hidden)
        checkpoint = dict(model=parent.state_dict(), schema=schema, dimensions=[g, c, hidden],
                          rules='synthetic', metadata={}, optimizer=None, torch_rng=torch.get_rng_state())
        original = deepcopy(checkpoint)
        generator = torch.Generator().manual_seed(17)
        global_rng, local_rng = torch.get_rng_state(), generator.get_state()
        off, off_ckpt = warm_start(checkpoint, ActorCritic, generator=generator)
        on, on_ckpt = warm_start(checkpoint, ActorCritic, generator=generator)
        self.assertTrue(torch.equal(global_rng, torch.get_rng_state()))
        self.assertTrue(torch.equal(local_rng, generator.get_state()))
        self.assertEqual(off_ckpt['schema'], on_ckpt['schema'])
        for key, tensor in parent.state_dict().items():
            self.assertTrue(torch.equal(tensor, original['model'][key]))
            for model in (off, on):
                expanded = model.state_dict()[key]
                self.assertTrue(torch.equal(tensor, expanded[:, :g] if key == 'state.0.weight' else expanded))
        for key in off_ckpt['model']:
            self.assertTrue(torch.equal(off_ckpt['model'][key], on_ckpt['model'][key]))
        self.assertEqual(torch.count_nonzero(off.state[0].weight[:, g:]).item(), 0)
        f = dict(global_=[0.1] * g, candidates=[[0.2] * c, [-0.3] * c], mask=[True, True])
        f['global'] = f.pop('global_')
        off_f = {**f, 'global': f['global'] + [0.] * 9}
        on_f = {**f, 'global': f['global'] + [0.693] * 9}
        pd, pv = parent([f])
        for model, features in ((off, off_f), (on, on_f)):
            dist, value = model([features])
            torch.testing.assert_close(pd.logits, dist.logits, rtol=1e-6, atol=1e-7)
            torch.testing.assert_close(pv, value, rtol=1e-6, atol=1e-7)
            # One BC loss backward, no optimizer or training update.
            (-dist.log_prob(torch.tensor([0])).mean()).backward()
        self.assertEqual(torch.count_nonzero(off.state[0].weight.grad[:, g:]).item(), 0)
        self.assertGreater(torch.count_nonzero(on.state[0].weight.grad[:, g:]).item(), 0)
        for a, b in zip(off.parameters(), on.parameters()):
            self.assertTrue(torch.equal(a, b))
        validate_checkpoint(off_ckpt, dict(schema=off_ckpt['schema'], rules='synthetic', features=off_f))
        # Standard checkpoint loader compatibility, no parallel training framework.
        import tempfile
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            path = Path(directory) / 'tiny.pt'
            torch.save(off_ckpt, path)
            restored, loaded = load_checkpoint(path)
            self.assertEqual(loaded['schema'], on_ckpt['schema'])
            for a, b in zip(restored.parameters(), off.parameters()):
                self.assertTrue(torch.equal(a, b))
        with self.assertRaisesRegex(ValueError, 'shop-v1'):
            warm_start(off_ckpt, ActorCritic)

    def test_comparison_rejections(self):
        schema = dict(version='shop-v1', ids=['a'], bonds=[], bands=[])
        f = dict(global_=[0] * 103, candidates=[[1] * 16], mask=[True])
        f['global'] = f.pop('global_')
        old = dict(schema=schema, samples=[dict(features=f, action=0, round=1, extra={'label': 'keep'})],
                   terminal=dict(schema=schema, features=f, summary={'victory': True}, observation={'own': 'keep'},
                                 partnerAudit={'actionSHA256': 'keep'}), arbitrary={'must': 'match'})
        new = deepcopy(old)
        new['schema'] = {**schema, 'version': 'shop-gold-v2'}
        new['terminal']['schema'] = new['schema']
        # deepcopy retains aliases, deliberately split feature objects before edits.
        new['terminal']['features'] = deepcopy(f)
        new['samples'][0]['features'] = deepcopy(f)
        for features in (new['terminal']['features'], new['samples'][0]['features']):
            features['global'] += [0, 0, 0]
        verify_recapture(old, new, 'off')
        new['samples'][0]['features']['global'][-1] = 0.693
        verify_recapture(old, new, 'on')
        with self.assertRaisesRegex(ValueError, 'OFF'):
            verify_recapture(old, new, 'off')
        mutations = [lambda x: x['samples'][0].update(action=1),
                     lambda x: x['samples'][0]['extra'].update(label='changed'),
                     lambda x: x['samples'][0]['features']['candidates'][0].__setitem__(0, 2),
                     lambda x: x['samples'][0]['features']['global'].__setitem__(0, 1),
                     lambda x: x['terminal']['summary'].update(victory=False),
                     lambda x: x['terminal']['partnerAudit'].update(actionSHA256='changed'),
                     lambda x: x['terminal']['observation'].update(own='changed'),
                     lambda x: x['arbitrary'].update(must='changed')]
        for mutation in mutations:
            changed = deepcopy(new)
            mutation(changed)
            with self.assertRaisesRegex(ValueError, 'differs'):
                verify_recapture(old, changed, 'on')
        with self.assertRaises(ValueError):
            same(True, 1)

    def test_source_and_size_fail_closed(self):
        from tempfile import TemporaryDirectory
        import gzip
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            source_bindings({'sourceSHA256': {'/other/source.py': 'bad'}}, HERE, HERE)
        with TemporaryDirectory(dir=HERE) as directory:
            file = Path(directory) / 'bomb.gz'
            file.write_bytes(gzip.compress(b' ' * 10000))
            with self.assertRaisesRegex(ValueError, 'decompressed'):
                unpack(file, 1000)

    def test_recapture_cli_mocked_no_game(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import gzip
        root = HERE.parent
        sources = {str(p): sha(p.read_bytes()) for folder in ('rl', 'coop') for p in (root / folder).iterdir()
                   if p.suffix in ('.py', '.mjs', '.json') and p.is_file()}
        # Supply a tiny collector fixture: test does not depend on preview-tools deployment.
        with TemporaryDirectory(dir=HERE) as directory:
            directory = Path(directory)
            collector = directory / 'collect-natural-coop-teachers.py'
            collector.write_text('# synthetic source fixture\n')
            sources[str(collector)] = sha(collector.read_bytes())
            schema = dict(version='shop-v1', ids=['a'], bonds=[], bands=[])
            f = {'global': [0] * 103, 'candidates': [[0] * 16], 'mask': [True]}
            terminal = dict(schema=schema, features=f, done=True, victory=True, rounds=14, rules=RULES,
                engineErrors=0, actionErrors=0, controllerErrors=0, controllerDiagnosticErrors=0,
                observation={'own': {'terminalOnly': True}},
                episode=dict(seed=30001, mode='coop', difficulty='ABYSS', budget=128,
                             partners=dict(pool='normal', split='train', poolSeed=313, fixed=TEAMS['normal'])),
                summary=dict(victory=True, seed=30001, modeId='mode_multi_abyss', errors=0, roundsPassed=14,
                             players=[dict(playerId='learner', victory=True, alive=True, roundsPassed=14)] +
                                     [dict(playerId=f'partner-{i}') for i in range(1, 4)]))
            old = dict(ok=True, schema=schema, rules=RULES, rejectedLabels=0, terminal=terminal,
                       samples=[dict(features=f, action=0, round=1)], teacher='synthetic-test')
            raw = (json.dumps(old) + '\n').encode()
            packed = gzip.compress(raw)
            old_file = directory / 'normal-30001.json.gz'
            old_file.write_bytes(packed)
            rows = [dict(seed=s, pool=p, status='not-run') for s in range(30001, 31025) for p in TEAMS]
            rows[0].update(status='complete', eligiblePositive=True, artifact=old_file.name, artifactSHA256=sha(packed), bytes=len(packed),
                           teamVictory=True, learnerVictory=True, learnerAlive=True, rounds=14,
                           learnerRounds=14, rejectedLabels=0, samples=1)
            manifest = dict(status='incomplete', finishedAt='synthetic-frozen', planned=2048,
                            pairedTrainingSeeds=1024, games=rows, sourceSHA256=sources)
            manifest_file = directory / 'frozen.json'
            manifest_file.write_text(json.dumps(manifest))
            selection = directory / 'selection.json'
            selection.write_text(json.dumps([str(old_file)]))
            output = directory / 'output'
            argv = ['--root', str(root), '--game', str(directory / 'nonexistent-game'),
                    '--collector', str(collector), '--manifest', str(manifest_file),
                    '--manifest-sha256', sha(manifest_file.read_bytes()), '--selection', str(selection),
                    '--out', str(output), '--seconds', '5']
            calls = []

            def fake_capture(command, environment, request, deadline, limit):
                calls.append((environment['GOLD_FEATURES_MODE'], request))
                new = json.loads(raw)
                new['schema']['version'] = new['terminal']['schema']['version'] = 'shop-gold-v2'
                for feature in [new['samples'][0]['features'], new['terminal']['features']]:
                    feature['global'] += [0, 0, 0] if environment['GOLD_FEATURES_MODE'] == 'off' else [0.693, 0, 0]
                return (json.dumps(new) + '\n').encode()

            with patch('recapture.capture', side_effect=fake_capture):
                main(argv)
                with self.assertRaises(FileExistsError):
                    main(argv)
            self.assertEqual([m for m, _ in calls], ['off', 'on'])
            self.assertEqual(calls[0][1], calls[1][1])
            result = json.loads((output / 'manifest.json').read_bytes())
            self.assertEqual(result['status'], 'complete')
            for row in result['results']:
                self.assertEqual(row['status'], 'verified')
                saved = (output / row['artifact']).read_bytes()
                self.assertEqual(sha(saved), row['artifactSHA256'])
                self.assertEqual(sha(gzip.decompress(saved)), row['rawSHA256'])
                self.assertEqual(row['sourceSHA256'], sha(packed))
            # Failures leave a bounded failed manifest, never an accepted dataset.
            failed = directory / 'failed'
            argv[argv.index('--out') + 1] = str(failed)
            with patch('recapture.capture', return_value=raw), self.assertRaisesRegex(ValueError, 'differs'):
                main(argv)
            rejection = json.loads((failed / 'manifest.json').read_bytes())
            self.assertEqual(rejection['status'], 'failed')
            self.assertLess(len(rejection['error']), 1300)
            self.assertEqual(rejection['results'][0]['status'], 'rejected')
            self.assertEqual(gzip.decompress((failed / rejection['results'][0]['artifact']).read_bytes()), raw)

    def test_expired_capture_never_starts(self):
        from unittest.mock import patch
        with patch('recapture.subprocess.Popen', side_effect=AssertionError('must not start')):
            with self.assertRaises(TimeoutError):
                capture([], {}, {}, 0, 1024)

    def test_python310_syntax(self):
        for path in HERE.glob('*.py'):
            ast.parse(path.read_text(), feature_version=(3, 10))


if __name__ == '__main__':
    unittest.main()
