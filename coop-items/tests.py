"""Only synthetic fixtures and tiny protocol subprocesses. No torch or game imports."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

import run as R

C = R.C
SCHEMA = dict(version='shop-v1', ids=[], bonds=[], bands=[])
CKPT = dict(schema=SCHEMA)
POOL_SHA = C.digest(json.dumps(C.parse((R.CODE / 'coop/pool.json').read_bytes()), separators=(',', ':')).encode())


def state(row, done, steps):
    audits = {f'partner-{i}': dict(rejected=0, rejections=[]) for i in (1, 2, 3)}
    diagnostics = dict(partnerAudit=audits, partnerActionErrors=0, controllerDiagnosticErrors=0,
                       rejectedActions=[], controllerBlockedMoves=0, controllerBlockedMoveReasons=[], filteredMoves=[])
    family = {'original-v1': 'upstream-bot', 'buy2-v1': 'first-offer-buy2', 'saver-v1': 'level-first-saver'}
    partners = dict(pool='normal' if row['pool'] == 'original' else 'mixed', split='eval', poolSeed=313,
                    fixed=C.TEAMS[row['pool']], manifest='coop-heuristics-v1', manifestSHA256=POOL_SHA,
                    seats=[dict(seat=i + 1, id=k, family=family[k]) for i, k in enumerate(C.TEAMS[row['pool']])])
    t = dict(ok=True, done=done, schema=SCHEMA, rules=C.RULES, steps=steps, rounds=2 if done else 1,
             controller=R.VERSIONS[row['controller']], reward=1 if done else 0, victory=done,
             episode=dict(seed=row['seed'], mode='coop', difficulty='ABYSS', budget=128, partners=partners),
             observation=dict(public=dict(phase='RESULT' if done else 'PREP'),
                              own=dict(playerId='learner', seat=0, alive=not done, lp=0 if done else 1)),
             features={'global': [0] * 90, 'candidates': [] if done else [[0] * 15], 'mask': [] if done else [True]},
             candidates=[] if done else [{'type': 'ready'}], summary=None, engineErrors=0, actionErrors=0,
             controllerErrors=0)
    if done:
        t.update(diagnostics)
        t['episode']['diagnostics'] = diagnostics.copy()
        t['summary'] = dict(seed=row['seed'], modeId='mode_multi_abyss', difficulty='ABYSS', victory=True,
                            errors=0, roundsPassed=2, reason='victory', players=[
            dict(playerId='learner' if i == 0 else f'partner-{i}', seat=i, victory=i != 0, alive=i != 0,
                 roundsPassed=2 if i else 0, eliminatedRound=None if i else 1, lp=1 if i else 0,
                 lineup=[], bonds=[], stats={}) for i in range(4)])
    return t


class FakeModel:
    def __init__(self):
        self.calls = 0

    def act(self, features, greedy=False):
        assert greedy is True and set(features) == {'global', 'candidates', 'mask'}
        self.calls += 1
        return 0, 0, 0


def fake_protocol(row, mode='normal', edit=None):
    class FakeProtocol:
        def __init__(self, command, env, out, deadline, byte_limit):
            assert command == ['node', '--no-warnings', '--loader', str(R.CODE / 'controller-v2/loader.mjs'),
                               str(R.CODE / 'coop/env.mjs'), '--game', '/synthetic/game']
            assert env['ARK_ITEM_CONTROLLER'] == row['controller']
            assert env['COOP_POOL'] == ('normal' if row['pool'] == 'original' else 'mixed')
            assert env['COOP_SPLIT'] == 'eval' and env['COOP_POOL_SEED'] == '313'
            assert env['COOP_TEAM'] == ','.join(C.TEAMS[row['pool']])
            assert 'NODE_OPTIONS' not in env
            self.out, self.bytes, self.limit, self.deadline = out, 0, byte_limit, deadline
            self.requests = 0
            self.raw = (out / 'protocol.jsonl').open('xb')
            self.actions = (out / 'actions.jsonl').open('xb')
            (out / 'stderr.log').write_bytes(b'')

        def charge(self, size):
            self.bytes += size
            C.require(self.bytes <= self.limit, 'Synthetic output limit')

        def request(self, request):
            self.requests += 1
            assert time.monotonic() < self.deadline
            if self.requests == 1:
                assert request == dict(cmd='reset', seed=row['seed'], mode='coop', difficulty='ABYSS', budget=128, teacher=False)
            else:
                assert request == dict(cmd='step', candidateIndex=0)
            if mode == 'timeout':
                raise TimeoutError('Synthetic response timeout')
            if mode == 'eof':
                raise ValueError('Unexpected protocol EOF')
            if mode == 'nonfinite':
                raw = b'{"ok": NaN}\n'
                self.raw.write(raw)
                self.charge(len(raw))
                return C.parse(raw), raw
            if mode in ('stop', 'runtime'):
                response = dict(ok=False, error='CONTROLLER_V2_STOP: retained' if mode == 'stop' else 'Unknown runtime error')
            elif mode == 'unknown':
                response = ['unexpected']
            else:
                response = state(row, self.requests == 2, self.requests - 1)
                if edit:
                    edit(response)
            raw = C.encoded(response)
            self.charge(len(raw))
            self.raw.write(raw)
            return response, raw

        def action(self, record):
            data = C.encoded(record)
            self.charge(len(data))
            self.actions.write(data)

        def finish(self):
            if mode == 'bad-close':
                raise ValueError('Close acknowledgement/EOF failure')
            data = C.encoded({'ok': True})
            self.charge(len(data))
            self.raw.write(data)

        def close(self):
            self.raw.close()
            self.actions.close()
    return FakeProtocol


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('COOP_ITEMS_TEST_TMP'))
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, row=None, mode='normal', edit=None, out=None):
        row = row or R.schedule()[0]
        model = FakeModel()
        result, seal = R.game_job(row, model, CKPT, Path('/synthetic/game'), out or self.out,
                                 time.monotonic() + 2, R.MIB, POOL_SHA, fake_protocol(row, mode, edit))
        return result, seal, model

    def prepared(self):
        rows = R.schedule()
        rows[0], seal, _ = self.job()
        R.write_new(self.out / 'manifest.json', {'games': R.schedule()})
        return rows, {rows[0]['id']: seal}, R.file_identity(self.out / 'manifest.json')

    def test_exact_128_schedule_and_counts(self):
        rows, seals = R.schedule(), {}
        self.assertEqual(len({r['id'] for r in rows}), 128)
        self.assertEqual({r['seed'] for r in rows}, set(range(1, 17)))
        self.assertNotIn(90001, {r['seed'] for r in rows})
        R.write_new(self.out / 'manifest.json', {'games': copy.deepcopy(rows)})
        manifest = R.file_identity(self.out / 'manifest.json')
        for i, row in enumerate(rows):
            rows[i], seals[row['id']], model = self.job(row)
            self.assertEqual(model.calls, 1)
        result = R.finalize(rows, self.out, seals, manifest, R.MIB, lambda: None)
        self.assertEqual((result['status'], result['complete'], result['teamWins'], result['learnerWins']),
                         ('complete', 128, 128, 0))
        self.assertTrue(all(c['denominator'] == 16 for c in result['cells'].values()))
        raw = (self.out / rows[0]['id'] / 'terminal.json').read_bytes()
        self.assertEqual(C.parse(raw), state(rows[0], True, 1))
        actions = C.parse((self.out / rows[0]['id'] / 'actions.jsonl').read_bytes())
        self.assertEqual(actions['candidates'], [{'type': 'ready'}])
        self.assertEqual(actions['candidateIndex'], 0)
        self.assertLess(sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file()), 4 * R.MIB)

    def test_failures_remain_in_denominator_and_sealed(self):
        rows, seals = R.schedule(), {}
        modes = ('stop', 'runtime', 'unknown', 'nonfinite', 'eof', 'timeout', 'bad-close')
        R.write_new(self.out / 'manifest.json', {'games': copy.deepcopy(rows)})
        for i, mode in enumerate(modes):
            rows[i], seals[rows[i]['id']], _ = self.job(rows[i], mode)
            self.assertEqual(rows[i]['status'], 'stopped' if mode == 'stop' else 'failed')
            self.assertFalse(rows[i]['eof'])
            self.assertIn('result.json', seals[rows[i]['id']])
        result = R.finalize(rows, self.out, seals, R.file_identity(self.out / 'manifest.json'), R.MIB, lambda: None)
        self.assertEqual(result['integrity'], 'verified')
        self.assertEqual((result['denominator'], result['stopped'], result['failed'], result['unrun'], result['teamWins']),
                         (128, 1, 6, 121, 0))
        self.assertTrue((self.out / rows[6]['id'] / 'terminal.json').exists())

    def test_every_response_controller_schema_steps_and_terminal_diagnostics(self):
        edits = [lambda s: s.update(controller='wrong'), lambda s: s.update(steps=10),
                 lambda s: s.update(rules='wrong'), lambda s: s['schema'].update(version='wrong'),
                 lambda s: s['episode']['partners'].update(poolSeed=314),
                 lambda s: s['episode']['partners'].update(fixed=['saver-v1'] * 3),
                 lambda s: s.update(controllerDiagnosticErrors=1) if s['done'] else None,
                 lambda s: s['summary'].update(errors=1) if s['done'] else None,
                 lambda s: s['episode']['diagnostics'].update(filteredMoves=[1]) if s['done'] else None]
        # Deep-copy before editing: the fixture's schema is intentionally shared like the real contract.
        for i, edit in enumerate(edits):
            def safe_edit(s, edit=edit):
                saved = copy.deepcopy(s)
                edit(saved)
                s.clear()
                s.update(saved)
            result, _, _ = self.job(R.schedule()[i], edit=safe_edit)
            self.assertEqual(result['status'], 'failed')

    def test_evidence_tamper_revokes_all_success_credit(self):
        mutations = ('raw', 'action', 'terminal', 'result', 'result-files', 'manifest',
                     'add', 'delete', 'empty-dir', 'root-add', 'symlink', 'episode-symlink', 'same-size')
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                d = self.out / mutation
                d.mkdir()
                rows = R.schedule()
                seals = {}
                for i in range(2):
                    rows[i], seals[rows[i]['id']], _ = self.job(rows[i], out=d)
                R.write_new(d / 'manifest.json', {'games': R.schedule()})
                md = R.file_identity(d / 'manifest.json')
                episode = d / rows[0]['id']
                names = dict(raw='protocol.jsonl', action='actions.jsonl', terminal='terminal.json', result='result.json')
                if mutation in names:
                    with (episode / names[mutation]).open('ab') as f:
                        f.write(b' ')
                elif mutation == 'result-files':
                    p = episode / 'result.json'
                    data = C.parse(p.read_bytes())
                    data['files']['terminal.json']['bytes'] += 1
                    p.write_bytes(C.encoded(data))
                elif mutation == 'manifest':
                    (d / 'manifest.json').write_bytes(b'{}')
                elif mutation == 'add':
                    (episode / 'extra').write_bytes(b'x')
                elif mutation == 'delete':
                    (episode / 'actions.jsonl').unlink()
                elif mutation == 'empty-dir':
                    (episode / 'empty').mkdir()
                elif mutation == 'root-add':
                    (d / 'extra').mkdir()
                elif mutation == 'symlink':
                    (episode / 'extra').symlink_to(episode / 'terminal.json')
                elif mutation == 'episode-symlink':
                    moved = self.out / ('moved-' + mutation)
                    episode.rename(moved)
                    episode.symlink_to(moved, target_is_directory=True)
                else:
                    p = episode / 'protocol.jsonl'
                    raw = p.read_bytes()
                    p.write_bytes(b'!' + raw[1:])
                result = R.finalize(rows, d, seals, md, R.MIB, lambda: None)
                self.assertEqual((result['integrity'], result['teamWins'], result['complete']), ('rejected', 0, 0))
                self.assertEqual(result['failed'], 2)
                self.assertTrue(all(r['creditRevoked'] for r in rows[:2]))

    def test_source_model_rules_guard_failure_revokes_credit(self):
        rows, seals, manifest = self.prepared()
        def fail():
            raise ValueError('Frozen source/checkpoint/rules drift')
        result = R.finalize(rows, self.out, seals, manifest, R.MIB, fail)
        self.assertEqual((result['teamWins'], result['complete'], result['integrity']), (0, 0, 'rejected'))

    def test_expired_audit_fails_closed(self):
        rows, seals, manifest = self.prepared()
        result = R.finalize(rows, self.out, seals, manifest, R.MIB, lambda: None, deadline=time.monotonic() - 1)
        self.assertEqual((result['teamWins'], result['integrity']), (0, 'rejected'))

    def test_mutation_during_final_guard_is_rejected(self):
        rows, seals, manifest = self.prepared()
        def drift():
            with (self.out / rows[0]['id'] / 'terminal.json').open('ab') as stream:
                stream.write(b' ')
        result = R.finalize(rows, self.out, seals, manifest, R.MIB, drift)
        self.assertEqual((result['teamWins'], result['complete'], result['integrity']), (0, 0, 'rejected'))

    def test_cli_missing_candidate_retains_128_without_game_or_model_load(self):
        parent = self.out / 'inputs' / 'parent.pt'
        parent.parent.mkdir()
        parent.write_bytes(b'not a model; must never deserialize')
        command = [sys.executable, '-B', str(R.HERE / 'run.py'), '--root', str(self.out),
                   '--parent', str(parent), '--parent-sha256', C.sha(parent), '--candidate', 'missing.pt',
                   '--candidate-sha256', '0' * 64, '--out', 'newdir', '--total-timeout', '2']
        result = subprocess.run(command, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        report = C.parse((self.out / 'newdir/summary.json').read_bytes())
        self.assertIn('candidate checkpoint missing', report['error'])
        self.assertEqual((report['denominator'], report['unrun'], report['complete']), (128, 128, 0))
        self.assertEqual({p.name for p in (self.out / 'newdir').iterdir()}, {'manifest.json', 'summary.json'})
        before = R.inventory(self.out / 'newdir')
        again = subprocess.run(command, capture_output=True, timeout=3)
        self.assertEqual(again.returncode, 1)
        self.assertEqual(before, R.inventory(self.out / 'newdir'))

    def test_checkpoint_admission_both_files_before_models(self):
        paths = {n: self.out / (n + '.pt') for n in R.MODELS}
        paths['parent'].write_bytes(b'parent')
        expected = dict(parent=C.sha(paths['parent']), candidate='0' * 64)
        with self.assertRaisesRegex(ValueError, 'candidate checkpoint missing'):
            R.check_files(paths, expected)
        paths['candidate'].write_bytes(b'candidate')
        with self.assertRaisesRegex(ValueError, 'candidate checkpoint SHA256 mismatch'):
            R.check_files(paths, expected)
        expected['candidate'] = C.sha(paths['candidate'])
        files = R.check_files(paths, expected)
        schema = dict(version='shop-v1', ids=[f'i{i:03}' for i in range(389)],
                      bonds=[f'b{i:02}' for i in range(15)], bands=[])
        class Actor:
            dimensions = [5267, 419, 128]
            def state_dict(self):
                return {'synthetic': SimpleNamespace(shape=(1,), dtype='torch.float32')}
            def eval(self):
                return self
            def requires_grad_(self, value):
                assert value is False
        def load(path):
            return Actor(), dict(schema=copy.deepcopy(schema), dimensions=[5267, 419, 128], rules=C.RULES,
                                 metadata={'version': path.name})
        L = SimpleNamespace(ActorCritic=Actor, load_checkpoint=load, digest=lambda m: 'parameter-hash',
                            torch=SimpleNamespace(isfinite=lambda v: SimpleNamespace(all=lambda:
                                SimpleNamespace(item=lambda: True))))
        loaded, identities = R.admit_models(L, paths, files)
        self.assertEqual(identities['candidate']['dimensions'], [5267, 419, 128])
        self.assertNotEqual(loaded['parent'][1]['metadata'], loaded['candidate'][1]['metadata'])
        Actor.dimensions = [5267, 419, 64]
        with self.assertRaisesRegex(ValueError, 'hidden128'):
            R.admit_models(L, paths, files)
        Actor.dimensions = [5267, 419, 128]
        schema['ids'][0] = 'bad'  # Sortedness/schema validation remains real.
        schema['ids'][1] = 'bad'
        with self.assertRaisesRegex(ValueError, 'vocabulary'):
            R.admit_models(L, paths, files)

    def test_original_protocol_real_pipe_without_game(self):
        programs = {
            'normal': ('import sys\nfor line in sys.stdin:\n print(\'{"ok":true}\',flush=True)\n if \'close\' in line: break', True),
            'extra': ('import sys\nfor line in sys.stdin:\n print(\'{"ok":true}\',flush=True)\n if \'close\' in line:\n  print(\'{"extra":1}\',flush=True)\n  break', False),
            'stderr': ('import sys\nprint("diagnostic",file=sys.stderr,flush=True)\nfor line in sys.stdin:\n print(\'{"ok":true}\',flush=True)\n if \'close\' in line: break', False),
            'malformed': ('print("not json",flush=True)', False),
            'eof': ('pass', False),
            'timeout': ('import time; time.sleep(1)', False),
            'bytes': ('print("x"*10000,flush=True)', False),
        }
        for name, (program, good) in programs.items():
            d = self.out / name
            d.mkdir()
            p = R.E.Protocol([sys.executable, '-B', '-c', program], C.clean_env(), d,
                             time.monotonic() + (0.15 if name == 'timeout' else 1), 4096)
            try:
                if good:
                    self.assertEqual(p.request({'cmd': 'reset'})[0], {'ok': True})
                    p.finish()
                else:
                    with self.assertRaises(Exception):
                        p.request({'cmd': 'reset'})
                        p.finish()
            finally:
                p.close()
            self.assertIsNotNone(p.process.poll())
            self.assertTrue((d / 'protocol.jsonl').exists())

    def test_loader_off_identity_and_exact_two_anchor_transform(self):
        # Import loader only, never rl/env, Match, Battle or any game module.
        script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {load,transform,VERSION} from './controller-v2/loader.mjs';
const source=fs.readFileSync('./rl/env.mjs','utf8');
const result={format:'module',source};
process.env.ARK_ITEM_CONTROLLER='off';
assert.equal(await load(new URL('./rl/env.mjs',import.meta.url).href,{},async()=>result),result);
const edited=transform(source);
assert.equal(edited,source.replace("import { control } from './controller.mjs';",
 'import { control } from '+JSON.stringify(new URL('./controller-v2/control.mjs',import.meta.url).href)+';')
 .replace("controller: 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1'",`controller: '${VERSION}'`));
assert.throws(()=>transform(source+' '),/SHA256/);
process.env.ARK_ITEM_CONTROLLER='v2';
assert.equal(await load('file:///unrelated/env.mjs',{},async()=>result),result);
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=R.CODE,
                                capture_output=True, timeout=2, env=C.clean_env())
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_source_identity_and_nooverwrite(self):
        identity = R.source_identity()
        self.assertEqual(identity['base'], R.BASE)
        self.assertIn('controller-v2/loader.mjs', identity['original'])
        self.assertIn('survivor-study/evaluate.py', identity['original'])
        self.assertIn('run.py', identity['implementation'])
        R.write_new(self.out / 'manifest.json', {'first': True})
        with self.assertRaises(FileExistsError):
            R.write_new(self.out / 'manifest.json', {})
        with self.assertRaisesRegex(ValueError, 'already exists'):
            C.new_output(self.out)


if __name__ == '__main__':
    unittest.main()
