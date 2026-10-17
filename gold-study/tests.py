"""Tiny synthetic fixtures / fake channels / Python mock children only; no game imports."""
import ast
from copy import deepcopy
import gzip
import json
from pathlib import Path
import resource
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from contracts import (C, E, N, R, CODE, HERE, Evidence, MODES, REVIEW, project,
                       environment, pool_sha, recheck, source_identity)
from data import admit, artifact
from training import fit
import evaluation as V
import run

SCHEMA = dict(version='shop-v1', ids=['a'], bonds=[], bands=[])
GOLD = {**SCHEMA, 'version': 'shop-gold-v2'}
GAME = dict(schema=SCHEMA, rules=C.RULES, dataSHA256='synthetic')
PIN = dict(rules=C.RULES, files=486)
POOL = pool_sha(CODE)


def features(gold=False, mode='off', done=False):
    return {'global': [0.1] * 103 + ([0, 0, .693] if mode == 'on' else [0, 0, 0]) * gold,
            'candidates': [] if done else [[0.1] * 16, [-.1] * 16], 'mask': [] if done else [True, True]}


def terminal(seed=1, pool='normal', split='eval', steps=1):
    audits = {f'partner-{i}': dict(rejected=0, rejections=[]) for i in (1, 2, 3)}
    diag = dict(partnerAudit=audits, partnerActionErrors=0, controllerDiagnosticErrors=0,
                rejectedActions=[], controllerBlockedMoves=0, controllerBlockedMoveReasons=[], filteredMoves=[])
    team = R.TEAMS[pool]
    families = dict(zip(('original-v1', 'buy2-v1', 'saver-v1'), ('upstream-bot', 'first-offer-buy2', 'level-first-saver')))
    return dict(ok=True, done=True, schema=SCHEMA, rules=C.RULES, steps=steps, rounds=14, reward=1, victory=True,
        episode=dict(seed=seed, mode='coop', difficulty='ABYSS', budget=128, diagnostics=diag,
            partners=dict(pool=pool, split=split, poolSeed=313, fixed=team, manifest='coop-heuristics-v1',
                manifestSHA256=POOL, seats=[dict(seat=i + 1, id=x, family=families[x]) for i, x in enumerate(team)])),
        summary=dict(seed=seed, modeId='mode_multi_abyss', difficulty='ABYSS', victory=True, errors=0,
            roundsPassed=14, reason='victory', players=[dict(playerId='learner' if i == 0 else f'partner-{i}',
                seat=i, victory=True, alive=True, roundsPassed=14, eliminatedRound=None, lp=1,
                lineup=[], bonds={}, stats={}) for i in range(4)]),
        observation=dict(public=dict(phase='RESULT'), own=dict(playerId='learner', seat=0, alive=True, lp=1)),
        features=features(done=True), candidates=[], engineErrors=0, actionErrors=0, controllerErrors=0, **diag)


def fixture(base):
    old_dir, rec = base / 'old', base / 'rec'
    old_dir.mkdir()
    rec.mkdir()
    rows = [dict(seed=s, pool=p, status='not-run') for s in range(30001, 31025) for p in R.TEAMS]
    results, selected = [], []
    for i in range(6):
        row = rows[2 * i]
        old = dict(ok=True, schema=SCHEMA, rules=C.RULES, terminal=terminal(row['seed'], split='train', steps=83),
                   teacher='upstream-bot/rehearsal=0', rejectedLabels=0,
                   samples=[dict(features=features(), action=j % 2, round=1) for j in range(83)])
        path = old_dir / f"normal-{row['seed']}.json.gz"
        packed = gzip.compress(C.encoded(old), mtime=0)
        path.write_bytes(packed)
        selected.append(str(path))
        row.update(status='complete', eligiblePositive=True, artifact=path.name, artifactSHA256=C.sha(path),
            bytes=len(packed), teamVictory=True, learnerVictory=True, learnerAlive=True, rounds=14,
            learnerRounds=14, rejectedLabels=0, samples=83)
        for mode in ('off', 'on'):
            new = deepcopy(old)
            new['schema'] = new['terminal']['schema'] = GOLD
            new['terminal']['features'] = features(gold=True, mode=mode, done=True)
            for s in new['samples']:
                s['features'] = features(gold=True, mode=mode)
            raw = C.encoded(new)
            name = f"normal-{row['seed']}-{mode}.json.gz"
            packed = gzip.compress(raw, mtime=0)
            (rec / name).write_bytes(packed)
            results.append(dict(mode=mode, seed=row['seed'], pool='normal', status='verified', source=str(path),
                sourceSHA256=row['artifactSHA256'], artifact=name, artifactSHA256=C.digest(packed), bytes=len(packed),
                rawSHA256=C.digest(raw), rawBytes=len(raw)))
    sources = {str(CODE / k): v for k, v in N.PINS['files'].items() if k.startswith(('rl/', 'coop/'))}
    collector = base / 'collect-natural-coop-teachers.py'
    collector.write_text('# synthetic collector; source binding mocked only in fixture tests\n')
    sources[str(collector)] = N.PINS['collector']['sha256']
    frozen = dict(status='incomplete', finishedAt='synthetic', planned=2048, pairedTrainingSeeds=1024,
                  games=rows, sourceSHA256=sources)
    frozen_raw = C.encoded(frozen)
    (old_dir / 'manifest.json').write_bytes(frozen_raw)
    (rec / 'source-manifest.json').write_bytes(frozen_raw)
    selection_raw = C.encoded(selected)
    (base / 'selection.json').write_bytes(selection_raw)
    (rec / 'selection.json').write_bytes(selection_raw)
    bindings = [dict(original=str(collector), local=str(collector), sha256=C.sha(collector))]
    manifest = dict(status='complete', finishedAt='synthetic', results=results, gameIdentity=PIN,
        fixedConfig=dict(split='train', poolSeed=313, teams=R.TEAMS, difficulty='ABYSS', budget=128),
        implementation={p.name: C.sha(p) for p in (CODE / 'gold-features').iterdir() if p.suffix in ('.mjs', '.py')},
        manifest=dict(path=str(old_dir / 'manifest.json'), sha256=C.digest(frozen_raw)), sources=bindings,
        selection=dict(path=str(base / 'selection.json'), sha256=C.digest(selection_raw)))
    (rec / 'manifest.json').write_bytes(C.encoded(manifest))
    return rec, manifest, collector


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.L = C.learner(CODE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.base = Path(self.temp.name)

    def tearDown(self):
        # No rglob monitor: check this test's owned files only after all mock children exit.
        self.assertLess(sum(p.stat().st_size for p in self.base.rglob('*') if p.is_file()), 5_000_000)
        self.temp.cleanup()

    def test_admission_whole_raw_and_guard(self):
        rec, m, collector = fixture(self.base)
        with patch.object(R, 'source_bindings', return_value=m['sources']):
            out = C.new_output(self.base / 'admit')
            samples, md = admit(rec, C.sha(rec / 'manifest.json'), CODE, collector, GAME, PIN, Evidence(out, 4_000_000))
        self.assertEqual((len(samples['off']), len(samples['on']), len(md['episodeGroups'])), (498, 498, 6))
        self.assertEqual(md['inputReviewStatus'], REVIEW)
        self.assertEqual((out / 'new-0-on.json.gz').read_bytes(), (rec / m['results'][1]['artifact']).read_bytes())
        self.assertEqual(samples['off'][0]['features']['global'][-3:], [0, 0, 0])
        self.assertEqual(samples['on'][0]['features']['global'][-1], .693)
        (rec / m['results'][1]['artifact']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Input drift'):
            recheck(md)

    def test_admission_rejects_status_pairs_raw_and_all_fields(self):
        rec, original, collector = fixture(self.base)
        edits = [lambda m: m.update(status='failed'), lambda m: m['results'].pop(),
                 lambda m: m['results'][0].update(status='pending'),
                 lambda m: m['results'][0].update(mode='unknown'),
                 lambda m: m['results'][0].update(rawSHA256='0' * 64),
                 lambda m: m['results'][0].update(sourceSHA256='0' * 64)]
        with patch.object(R, 'source_bindings', return_value=original['sources']):
            for i, edit in enumerate(edits):
                m = deepcopy(original)
                edit(m)
                (rec / 'manifest.json').write_bytes(C.encoded(m))
                with self.assertRaises((ValueError, KeyError)):
                    admit(rec, C.sha(rec / 'manifest.json'), CODE, collector, GAME, PIN,
                          Evidence(C.new_output(self.base / f'reject{i}'), 4_000_000))
        old = C.parse(gzip.decompress(Path(original['results'][0]['source']).read_bytes()))
        new = C.parse(gzip.decompress((rec / original['results'][1]['artifact']).read_bytes()))
        for mutate in (lambda d: d['samples'][0].update(action=1),
                       lambda d: d['samples'][0]['features']['candidates'][0].__setitem__(0, 99),
                       lambda d: d['terminal']['observation']['own'].update(lp=99),
                       lambda d: d.update(unexpected=True)):
            changed = deepcopy(new)
            mutate(changed)
            with self.assertRaises(ValueError):
                R.verify_recapture(old, changed, 'on')

    def test_gzip_eof_crc_and_duplicate_json(self):
        path = self.base / 'raw.gz'
        good = gzip.compress(b'{"ok":true}\n')
        for data in (good[:-3], good + b'tail', good + gzip.compress(b'{}\n'),
                     gzip.compress(b'{"x":1,"x":2}\n'), gzip.compress(b'{}')):
            path.write_bytes(data)
            with self.assertRaises((ValueError, OSError, EOFError)):
                artifact(path, dict(bytes=len(data), artifactSHA256=C.digest(data)))

    def test_projection_and_real_contracts_leave_raw_actual_unchanged(self):
        state = terminal()
        state['schema'], state['features'] = GOLD, features(gold=True, done=True, mode='on')
        rejection = dict(diagnostic=True, reason='pure-validation', result={'error': 'HAND_FULL'})
        state['controllerErrors'] = state['controllerDiagnosticErrors'] = 1
        state['rejectedActions'] = [rejection]
        state['episode']['diagnostics'].update(controllerDiagnosticErrors=1, rejectedActions=[rejection])
        before = C.encoded(state)
        view = project(state, GOLD, 'on')
        E.state_contract(view, V.schedule()[0], {'schema': SCHEMA}, 1, POOL)
        C.terminal_contract(view, seed=1, steps=1, schema=SCHEMA)  # default teacher=False
        self.assertEqual(before, C.encoded(state))
        self.assertIs(view['features']['candidates'], state['features']['candidates'])
        for mode in ('off', 'invalid'):
            with self.assertRaises(ValueError):
                project(state, GOLD, mode)
        for edit in (lambda s: s['schema'].update(version='unknown'),
                     lambda s: s['features']['global'].pop(),
                     lambda s: s['features']['global'].__setitem__(-1, -1),
                     lambda s: s['features']['global'].__setitem__(-1, float('nan'))):
            bad = deepcopy(state)
            edit(bad)
            with self.assertRaises(ValueError):
                project(bad, GOLD, 'on')

    def test_game_job_fake_protocol_actual_actor_input_and_raw(self):
        terminal_state = terminal()
        terminal_state['schema'], terminal_state['features'] = GOLD, features(gold=True, mode='on', done=True)
        state = deepcopy(terminal_state)
        state.update(done=False, steps=0, rounds=1, summary=None, reward=0, victory=False,
                     features=features(gold=True, mode='on'), candidates=[{'type': 'ready'}, {'type': 'refresh'}])
        del state['episode']['diagnostics'], state['partnerAudit']
        replies = [state, terminal_state]
        raw = [json.dumps(s, indent=None).encode() + b'  \n' for s in replies]
        seen = []

        class FakeProtocol:
            def __init__(inner, command, env, out, deadline, limit):
                self.assertIn('--no-warnings', command)
                self.assertIn(str(CODE / 'gold-features/loader.mjs'), command)
                self.assertEqual(env['GOLD_FEATURES_MODE'], 'on')
                self.assertEqual(env['COOP_SPLIT'], 'eval')
                inner.bytes, inner.i, inner.out = 0, 0, out
                inner.raw = (out / 'protocol.jsonl').open('xb')
            def request(inner, command):
                seen.append(command)
                i = inner.i
                inner.i += 1
                inner.raw.write(raw[i])
                return replies[i], raw[i]
            def charge(inner, size):
                inner.bytes += size
            def action(inner, value):
                self.assertEqual(value['candidates'], state['candidates'])
                self.assertEqual(value['mask'], [True, True])
            def finish(inner):
                seen.append('EOF')
            def close(inner):
                inner.raw.close()

        class Model:
            def act(inner, f, greedy):
                self.assertIs(f, state['features'])
                self.assertEqual(len(f['global']), 106)
                return 0, 0, 0

        before = [C.encoded(s) for s in replies]
        with patch.object(E, 'Protocol', FakeProtocol):
            result = V.game_job(V.schedule()[64], Model(), {'schema': GOLD}, CODE, self.base, self.base,
                                time.monotonic() + 2, 100000, POOL)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['eof'])
        self.assertEqual(seen[-1], 'EOF')
        directory = self.base / result['id']
        self.assertEqual((directory / 'terminal.json').read_bytes(), raw[-1])
        self.assertEqual((directory / 'protocol.jsonl').read_bytes(), b''.join(raw))
        self.assertEqual(before, [C.encoded(s) for s in replies])
        class NoEOF(FakeProtocol):
            def finish(inner):
                raise ValueError('mock missing EOF')
        with patch.object(E, 'Protocol', NoEOF):
            failed = V.game_job(V.schedule()[64], Model(), {'schema': GOLD}, CODE, self.base,
                                C.new_output(self.base / 'no-eof'), time.monotonic() + 2, 100000, POOL)
        self.assertEqual(failed['status'], 'failed')
        self.assertFalse(failed['eof'])
        self.assertEqual((self.base / 'no-eof' / failed['id'] / 'terminal.json').read_bytes(), raw[-1])

    def test_original_protocol_with_python_mock_children(self):
        for i, suffix in enumerate(('', 'sys.stdout.write("extra")', 'sys.stderr.write("error")')):
            out = C.new_output(self.base / str(i))
            script = 'import sys\nsys.stdin.readline()\nsys.stdout.write(\' {"ok":true}  \\n\');sys.stdout.flush()\n' + suffix
            protocol = E.Protocol([sys.executable, '-c', script], environment(CODE, 'off', 'original'),
                                  out, time.monotonic() + 2, 10000)
            try:
                if suffix:
                    with self.assertRaises(ValueError):
                        protocol.finish()
                else:
                    protocol.finish()
            finally:
                protocol.close()
            self.assertTrue((out / 'protocol.jsonl').read_bytes().startswith(b' {"ok":true}  \n'))

    def test_original_200_updates_and_eval_gate_scheduler(self):
        L = self.L
        parent = self.base / 'all.pt'
        L.save_checkpoint(parent, L.ActorCritic(103, 16, 4), SCHEMA, C.RULES, {'synthetic': True})
        samples = {mode: [dict(features=features(gold=True, mode=mode), action=i % 2) for i in range(498)]
                   for mode in ('off', 'on')}
        md = dict(schema=GOLD, labels=[dict(episodeGroup=i // 83 + 30001) for i in range(498)],
                  episodeGroups=list(range(30001, 30007)), inputSHA256='synthetic', inputFiles={}, rawFileSets={})
        out = C.new_output(self.base / 'trained')
        evidence = Evidence(out, 4_000_000)
        evidence.json('input-metadata.json', md)
        started = time.monotonic()
        report = fit(L, parent, samples, md, {'synthetic': True}, GAME, evidence)
        self.assertLess(time.monotonic() - started, 5, 'Tiny original 200 updates per arm must be <5s')
        evidence.verify()
        C.write(out / 'train.json', report)
        self.assertEqual(len(set(r['initialParameterSHA256'] for r in report['models'].values())), 1)
        for name in ('trainedOff', 'trainedOn'):
            r = report['models'][name]
            self.assertEqual((r['actualUpdates'], r['actualDraws']), (200, 6800))
            self.assertEqual(sum(r['actualLabelDrawCounts']), 6800)
            self.assertEqual(r['actualLabelDrawCounts'], [14] * 326 + [13] * 172)
            self.assertTrue(r['criticHeadUnchanged'])
        self.assertEqual(report['models']['trainedOff']['actualOrderSHA256'], report['models']['trainedOn']['actualOrderSHA256'])
        self.assertEqual(report['models']['warmBaseline']['actualDraws'], 0)
        baseline, _ = L.load_checkpoint(out / 'warmBaseline.pt')
        off_dist, off_value = baseline([samples['off'][0]['features']])
        on_dist, on_value = baseline([samples['on'][0]['features']])
        L.torch.testing.assert_close(off_dist.logits, on_dist.logits, rtol=1e-6, atol=1e-7)
        L.torch.testing.assert_close(off_value, on_value, rtol=1e-6, atol=1e-7)
        with self.assertRaises(FileExistsError):
            evidence.put('warmBaseline.pt', b'must not overwrite')
        rows = V.schedule()
        def job(row, *args):
            return {**row, 'status': 'failed' if row['seed'] == 2 else 'complete',
                    'teamVictory': True, 'learnerIndividualVictory': False}
        with patch.object(V, 'game_job', side_effect=job):
            V.evaluate(L, out, parent, CODE, self.base, GAME, C.new_output(self.base / 'eval'),
                       {'synthetic': True}, rows, time.monotonic() + 2, lambda: None)
        s = V.summary(rows)
        self.assertEqual((s['denominator'], s['complete'], s['failed'], s['learnerWins']), (96, 90, 6, 0))
        rows = V.schedule()
        with patch.object(V, 'game_job') as jobs:
            V.evaluate(L, out, parent, CODE, self.base, GAME, C.new_output(self.base / 'expired'),
                       {'synthetic': True}, rows, 0, lambda: None)
            jobs.assert_not_called()
        self.assertEqual(V.summary(rows)['unrun'], 96)
        with patch.object(V, 'game_job') as jobs:
            with self.assertRaisesRegex(ValueError, 'drift'):
                V.evaluate(L, out, parent, CODE, self.base, GAME, C.new_output(self.base / 'drift'),
                    {'synthetic': True}, V.schedule(), 0, lambda: (_ for _ in ()).throw(ValueError('source drift')))
            jobs.assert_not_called()
        # Changed inputs are rejected before any subsequent child starts.
        changed = out / 'draw-plan.json'
        with changed.open('ab') as stream:
            stream.write(b' ')
        with patch.object(V, 'game_job') as jobs:
            with self.assertRaisesRegex(ValueError, 'drift'):
                V.evaluate(L, out, parent, CODE, self.base, GAME, C.new_output(self.base / 'input-drift'),
                           {'synthetic': True}, V.schedule(), time.monotonic() + 1, lambda: None)
            jobs.assert_not_called()

    def test_cli_preflight_failure_preserves_96_plan_nooverwrite(self):
        argv = ['eval', '--root', str(self.base), '--experiment-code', str(CODE), '--parent', 'missing',
                '--parent-sha256', '0' * 64, '--out', 'failed', '--trained', 'missing', '--dev-only', '--total-timeout', '2']
        with patch('subprocess.Popen', side_effect=AssertionError('No real child permitted')):
            self.assertEqual(run.main(argv), 1)
            with self.assertRaises(ValueError):
                run.main(argv)
        self.assertEqual(C.parse((self.base / 'failed/summary.json').read_bytes())['unrun'], 96)
        self.assertEqual(len(C.parse((self.base / 'failed/manifest.json').read_bytes())['games']), 96)
        def invalidated(*args, **kwargs):
            rows = args[8]
            rows[0].update(status='complete', teamVictory=True, learnerIndividualVictory=True)
            raise ValueError('source drift after games')
        # Mock all preflight I/O: this tests only failed-result accounting, never a game/static loader.
        full_schema = dict(version='shop-v1', ids=[f'i{i:03}' for i in range(381)],
                           bonds=[f'b{i:02}' for i in range(23)], bands=[f'r{i:02}' for i in range(40)])
        argv[argv.index('--out') + 1] = 'invalidated'
        with patch.object(N, 'check_parent'), patch.object(R, 'game_identity', return_value=PIN), \
             patch.object(C, 'game_identity', return_value=(self.base / 'game', {'schema': full_schema})), \
             patch.object(C, 'learner', return_value=self.L), patch.object(run, 'evaluate', side_effect=invalidated), \
             patch.object(run.subprocess, 'run') as version:
            version.return_value.stdout, version.return_value.stderr = b'v24.0.0\n', b''
            self.assertEqual(run.main(argv), 1)
        invalid = C.parse((self.base / 'invalidated/summary.json').read_bytes())
        self.assertEqual((invalid['failed'], invalid['unrun'], invalid['teamWins']), (1, 95, 0))
        with self.assertRaises(ValueError):
            Evidence(self.base, 1).put('too-big', b'12')

    def test_syntax_pins_and_schedule(self):
        for path in HERE.glob('*.py'):
            ast.parse(path.read_text(), feature_version=(3, 10))
        self.assertEqual(source_identity(CODE)['base'], '2f4842a844aa17adea9360b4f85c6285e6a97319')
        rows = V.schedule()
        self.assertEqual(len(rows), 96)
        self.assertEqual({r['seed'] for r in rows}, set(range(1, 17)))
        self.assertEqual({(r['model'], r['goldMode']) for r in rows}, set(MODES.items()))
        self.assertEqual(V.summary(rows)['independentSeedGroups'], 16)


if __name__ == '__main__':
    result = unittest.main(exit=False)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f'maxRSS_KiB={peak}; synthetic only')
    if peak >= 1024 * 1024:
        raise SystemExit('RSS exceeded 1 GiB')
    raise SystemExit(not result.result.wasSuccessful())
