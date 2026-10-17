"""Synthetic only: original tiny BC, Python protocol peers, and filesystem drift."""
import ast
import gzip
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest

from contracts_fit import (C, D, E, CODE, HERE, ARMS, identity, check_parent,
    project_off, recheck_inputs, safe_path, source_identity)
from training import train_arm, epoch_orders
import evaluation as V

TMP = Path(os.environ['FIT_TEST_TMP'])


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def test_original_bc_independent_1000_3000(self):
        L = C.learner(CODE)  # Only once per process; original torch interop-thread setup.
        L.torch.manual_seed(4)
        parent = L.ActorCritic(2, 2, 2)
        samples = [dict(features={'global': [i / 498, 0.], 'candidates': [[1., 0.], [-1., 0.]],
                                 'mask': [True, True]}, action=0) for i in range(498)]
        parent_sha = L.digest(parent)
        prefix_sha = None
        for name, epochs in ARMS.items():
            model, opt, record, draws = train_arm(L, parent, samples, epochs)
            self.assertEqual(record['actualUpdates'], epochs * 200)
            self.assertEqual(record['actualDraws'], epochs * 6800)
            self.assertEqual(record['initialParameterSHA256'], parent_sha)
            self.assertEqual(record['initialCriticHeadSHA256'], record['finalCriticHeadSHA256'])
            self.assertEqual(record['after']['accuracy'], 1.)
            self.assertEqual(record['training']['samples'], 6800)
            self.assertEqual(draws.accesses, sum(epoch_orders(L, epochs), []))
            prefix = C.digest(C.encoded(draws.accesses[:34000]))
            if prefix_sha:
                self.assertEqual(prefix_sha, prefix)
            prefix_sha = prefix
            path = self.out / (name + '.pt')
            L.save_checkpoint(path, model, {'version': 'synthetic'}, C.RULES, record, opt)
            restored, ckpt = L.load_checkpoint(path)
            self.assertEqual(L.digest(restored), L.digest(model))
            self.assertEqual(ckpt['metadata'], record)
            self.assertTrue(all(int(v['step']) == epochs * 200 for v in ckpt['optimizer']['state'].values()))
        self.assertEqual(L.digest(parent), parent_sha)

    def test_off_projection_full_shape_without_real_data(self):
        schema = dict(version='shop-gold-v2', ids=[f'i{i:03}' for i in range(381)],
                      bonds=[f'b{i:02}' for i in range(23)], bands=[f'a{i:02}' for i in range(40)])
        game = dict(schema={**schema, 'version': 'shop-v1'})
        features = dict(global_=[.25] * 5267 + [0.] * 1143)
        features = {'global': features['global_'], 'candidates': [[.5] * 419], 'mask': [True]}
        sample = dict(features=features, action=0)
        paired = dict(off=[sample] * 498, on=[None] * 498)
        md = dict(schema=schema, labels=[{}] * 498, episodeGroups=list(range(6)))
        result = project_off(paired, md, game)
        self.assertEqual(len(result), 498)
        self.assertEqual(result[0]['features']['global'], [.25] * 5267)
        self.assertIs(result[0]['features']['candidates'], features['candidates'])
        self.assertIs(result[0]['features']['mask'], features['mask'])
        self.assertEqual(len(features['global']), 6410)
        features['global'][-1] = 1.
        with self.assertRaises(ValueError):
            project_off(paired, md, game)

    def test_artifact_eof_sha_crc(self):
        raw = C.encoded(dict(synthetic=True, terminal={'done': True}))
        path = self.out / 'demo.json.gz'
        packed = gzip.compress(raw, mtime=0)
        path.write_bytes(packed)
        row = dict(artifactSHA256=C.digest(packed), bytes=len(packed), rawSHA256=C.digest(raw), rawBytes=len(raw))
        self.assertEqual(D.artifact(path, row, gold=True)[0]['terminal'], {'done': True})
        for damaged in (packed[:-1], packed + b'garbage', gzip.compress(raw + raw, mtime=0)):
            path.write_bytes(damaged)
            with self.assertRaises((ValueError, EOFError, OSError)):
                D.artifact(path, {**row, 'artifactSHA256': C.digest(damaged), 'bytes': len(damaged)}, gold=True)

    def test_input_drift_and_symlink_ancestors(self):
        rawdir = self.out / 'raw'
        rawdir.mkdir()
        p = rawdir / 'x.json.gz'
        p.write_bytes(b'synthetic')
        md = dict(inputFiles={str(p): C.sha(p)}, rawFileSets={str(rawdir): ['x.json.gz']})
        recheck_inputs(md, [str(p)])
        p.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            recheck_inputs(md, [str(p)])
        p.write_bytes(b'synthetic')
        (rawdir / 'orphan.json.gz').write_bytes(b'')
        with self.assertRaises(ValueError):
            recheck_inputs(md, [str(p)])
        (rawdir / 'orphan.json.gz').unlink()
        moved = self.out / 'moved'
        rawdir.rename(moved)
        rawdir.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            recheck_inputs(md, [str(p)])
        with self.assertRaises(ValueError):
            safe_path(rawdir / '..' / 'x')

    def result_fixture(self):
        rows = V.schedule()
        row = rows[0]
        directory = self.out / row['id']
        directory.mkdir()
        for name in ('protocol.jsonl', 'actions.jsonl', 'stderr.log', 'terminal.json'):
            (directory / name).write_bytes(b'{}\n' if name != 'stderr.log' else b'')
        row.update(status='complete', teamVictory=True, learnerIndividualVictory=False, eof=True,
                   files={p.name: identity(p) for p in directory.iterdir()})
        C.write(directory / 'result.json', row)
        row['resultFile'] = identity(directory / 'result.json')
        C.write(self.out / 'manifest.json', {'synthetic': True})
        C.write(self.out / 'summary.json', {'status': 'running'})
        control = {'manifest.json': identity(self.out / 'manifest.json')}
        return rows, directory, control

    def test_payload_result_and_directory_drift_revoke_all(self):
        rows, directory, control = self.result_fixture()
        V.verify_results(rows, self.out, control)
        self.assertEqual(V.summary(rows)['teamWins'], 1)
        self.assertEqual(V.summary(rows)['learnerWins'], 0)
        result_path = directory / 'result.json'
        original_result = result_path.read_bytes()
        original_identity = rows[0]['resultFile']
        tampered = C.parse(original_result)
        tampered['teamVictory'] = False
        C.write(result_path, tampered)
        rows[0]['resultFile'] = identity(result_path)
        with self.assertRaises(ValueError):
            V.verify_results(rows, self.out, control)
        result_path.write_bytes(original_result)
        rows[0]['resultFile'] = original_identity
        for name in ('protocol.jsonl', 'terminal.json', 'actions.jsonl', 'stderr.log', 'result.json'):
            path = directory / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                V.verify_results(rows, self.out, control)
            path.write_bytes(original)
        (directory / 'extra').write_bytes(b'')
        with self.assertRaises(ValueError):
            V.verify_results(rows, self.out, control)
        (directory / 'extra').unlink()
        moved = self.out / 'moved'
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            V.verify_results(rows, self.out, control)
        V.invalidate(rows, 'source/parent/input/model/evidence drift')
        counts = V.summary(rows)
        self.assertEqual((counts['denominator'], counts['technicalFailures'], counts['unrun']), (96, 1, 95))
        self.assertEqual(counts['teamWins'], 0)
        self.assertTrue(all(c['denominator'] == 16 for c in counts['cells'].values()))

    def test_scheduler_failure_and_unrun_denominator(self):
        rows = V.schedule()
        models = {n: (None, None) for n in V.MODELS}
        seen = []
        def fake(row, *args):
            seen.append(row['id'])
            if row['seed'] == 1:
                raise RuntimeError('synthetic technical failure')
            return {**row, 'status': 'complete', 'teamVictory': True, 'learnerIndividualVictory': False}
        V.dispatch(rows, models, None, None, self.out, time.monotonic() + 2, 4, 1, 65536, None, job=fake)
        counts = V.summary(rows)
        self.assertEqual(len(set(seen)), 96)
        self.assertEqual((counts['complete'], counts['technicalFailures'], counts['learnerWins']), (90, 6, 0))
        rows = V.schedule()
        V.dispatch(rows, models, None, None, self.out, time.monotonic() - 1, 4, 1, 65536, None, job=fake)
        self.assertEqual(V.summary(rows)['unrun'], 96)

    def test_original_protocol_python_peers(self):
        for suffix in ('', 'print("extra", flush=True)', 'import sys; sys.stderr.write("error")', 'raise SystemExit(1)'):
            directory = self.out / str(len(list(self.out.iterdir())))
            directory.mkdir()
            script = 'import sys\nfor line in sys.stdin:\n print(\'{"ok":true}\', flush=True)\n break\n' + suffix
            channel = E.Protocol([sys.executable, '-B', '-c', script], C.clean_env(), directory,
                                 time.monotonic() + 1, 65536)
            try:
                if suffix:
                    with self.assertRaises(ValueError):
                        channel.finish()
                else:
                    channel.finish()
            finally:
                channel.close()
            self.assertTrue((directory / 'protocol.jsonl').read_bytes().startswith(b'{"ok":true}\n'))
        directory = self.out / 'timeout'
        directory.mkdir()
        channel = E.Protocol([sys.executable, '-B', '-c', 'import time; time.sleep(2)'], C.clean_env(),
                             directory, time.monotonic() + .05, 65536)
        try:
            with self.assertRaises(TimeoutError):
                channel.finish()
        finally:
            channel.close()
        self.assertIsNotNone(channel.process.poll())

    def test_source_and_python310_contract(self):
        source = source_identity(CODE)
        self.assertEqual(len(source['sha256']), 64)
        for path in HERE.glob('*.py'):
            ast.parse(path.read_text(), feature_version=(3, 10))
        code = ast.parse((HERE / 'evaluation.py').read_text())
        self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
                            isinstance(n.func.value, ast.Name) and n.func.value.id == 'E' and
                            n.func.attr == 'game_job' for n in ast.walk(code)))

    def test_cli_missing_parent_keeps_96_without_node_or_torch(self):
        # The fixed parent guard runs before Node/static vocabulary/learner import.
        result = subprocess.run([sys.executable, '-B', str(HERE / 'run.py'), 'eval', '--dev-only',
            '--root', str(self.out), '--out', 'eval', '--trained', 'absent'],
            capture_output=True, timeout=2, env={**os.environ, 'PATH': '/nonexistent'})
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        report = C.parse((self.out / 'eval/summary.json').read_bytes())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['creditGranted'])
        self.assertEqual((report['denominator'], report['unrun'], report['teamWins']), (96, 96, 0))
        manifest = C.parse((self.out / 'eval/manifest.json').read_bytes())
        self.assertEqual(len(manifest['games']), 96)
        self.assertIn('Input file/size', report['error'])

    def test_fixed_parent_rejects_wrong_bytes_and_symlink(self):
        path = self.out / 'experiments/bc-expansion-v1/all.pt'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'not the designated checkpoint')
        with self.assertRaises(ValueError):
            check_parent(self.out)
        path.unlink()
        path.symlink_to(self.out / 'missing')
        with self.assertRaises(ValueError):
            check_parent(self.out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
