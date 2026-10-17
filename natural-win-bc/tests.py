"""Synthetic data only. Never create an Environment, Match or Battle."""
import ast
import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from support import C, E, CODE, HERE, PINS, MIB, Evidence, source_identity, game_identity, check_parent
from admission import PLAN, admit, frozen, complete_contract, read_artifact, recheck
from training import train
from evaluation import schedule, summary, evaluate

SCHEMA = dict(version='shop-v1', ids=[], bonds=[], bands=[])
GAME = dict(schema=SCHEMA, rules=C.RULES, dataSHA256='d' * 64)
POOL = 'a' * 64


def terminal(seed=30001, alive=True, split='train', pool='normal', steps=2):
    audits = {f'partner-{i}': dict(rejected=0, rejections=[]) for i in (1, 2, 3)}
    diag = dict(partnerAudit=audits, partnerActionErrors=0, controllerDiagnosticErrors=0,
                rejectedActions=[], controllerBlockedMoves=0, controllerBlockedMoveReasons=[], filteredMoves=[])
    team = C.TEAMS['original' if pool == 'normal' else 'mixed']
    families = dict(zip(('original-v1', 'buy2-v1', 'saver-v1'), ('upstream-bot', 'first-offer-buy2', 'level-first-saver')))
    partners = dict(pool=pool, split=split, poolSeed=313, fixed=team, manifest='coop-heuristics-v1',
                    manifestSHA256=POOL, seats=[dict(seat=i + 1, id=x, family=families[x]) for i, x in enumerate(team)])
    players = [dict(playerId='learner' if i == 0 else f'partner-{i}', seat=i, victory=alive or i > 0,
                    alive=alive or i > 0, roundsPassed=14 if alive or i > 0 else 10,
                    eliminatedRound=None if alive or i > 0 else 11, lp=1 if alive or i > 0 else 0,
                    lineup=[], bonds={}, stats={}) for i in range(4)]
    return dict(done=True, schema=SCHEMA, rules=C.RULES, steps=steps, rounds=14, reward=1, victory=True,
                episode=dict(seed=seed, mode='coop', difficulty='ABYSS', budget=128, diagnostics=diag, partners=partners),
                summary=dict(seed=seed, modeId='mode_multi_abyss', difficulty='ABYSS', victory=True, errors=0,
                             roundsPassed=14, reason='victory', players=players),
                observation=dict(public=dict(phase='RESULT'), own=dict(playerId='learner', seat=0, alive=alive, lp=1 if alive else 0)),
                features={'global': [0] * 90, 'candidates': [], 'mask': []}, candidates=[],
                engineErrors=0, actionErrors=0, controllerErrors=0, **diag)


def demo(seed=30001, pool='normal', alive=True):
    return dict(ok=True, schema=SCHEMA, rules=C.RULES, terminal=terminal(seed, alive, pool=pool),
                samples=[dict(features={'global': [0] * 90, 'candidates': [[0] * 15, [1] * 15],
                                        'mask': [True, True]}, action=i % 2, round=i + 1) for i in range(2)],
                teacher='upstream-bot/rehearsal=0', rejectedLabels=0)


def save_game(directory, row, data):
    path = directory / f"{row['pool']}-{row['seed']}.json.gz"
    path.write_bytes(gzip.compress(C.encoded(data), mtime=0))
    p = data['terminal']['summary']['players'][0]
    row.update(status='complete', startedAt='synthetic-start', finishedAt='synthetic-end', artifact=path.name,
               artifactSHA256=C.sha(path), bytes=path.stat().st_size, teamVictory=True, learnerVictory=p['victory'],
               learnerAlive=p['alive'], rounds=14, learnerRounds=p['roundsPassed'], rejectedLabels=0,
               samples=len(data['samples']), eligiblePositive=p['victory'] and p['alive'])


def fixture(base):
    directory = base / 'batch'
    directory.mkdir()
    rows = [dict(seed=s, pool=p, status='not-run') for s, p in PLAN]
    for i in range(3):
        save_game(directory, rows[i], demo(rows[i]['seed'], rows[i]['pool'], alive=i < 2))
    rows[3].update(status='failed', error='synthetic collection timeout', startedAt='start', finishedAt='end')
    sources = {'/synthetic/experiment-code/' + k: v for k, v in PINS['files'].items() if k.startswith(('rl/', 'coop/'))}
    sources['/synthetic/preview-tools/' + PINS['collector']['name']] = PINS['collector']['sha256']
    manifest = dict(status='incomplete', startedAt='start', finishedAt='end', planned=2048,
                    pairedTrainingSeeds=1024, sourceSHA256=sources, games=rows)
    (directory / 'manifest.json').write_bytes(C.encoded(manifest))
    return directory, manifest


class Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.L = C.learner(CODE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('NATURAL_TEST_TMP'))
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_batch_full_denominator_positive_filter_groups_and_drift(self):
        directory, m = fixture(self.base)
        out = C.new_output(self.base / 'out')
        samples, md = admit(directory, C.sha(directory / 'manifest.json'), GAME, POOL, Evidence(out))
        self.assertEqual((md['plannedGames'], md['plannedEpisodeGroups']), (2048, 1024))
        self.assertEqual(md['statusCounts'], {'complete': 3, 'failed': 1, 'not-run': 2044})
        self.assertEqual((len(samples), md['positiveGames'], md['eligibleGroupCount']), (4, 2, 1))
        self.assertEqual(md['eligibleEpisodeGroups'], [30001])
        self.assertTrue(all(set(s) == {'features', 'action'} for s in samples))
        self.assertEqual(len(md['games']), 2048)
        negative = C.parse(gzip.decompress((out / 'audit-normal-30002.json.gz').read_bytes()))
        self.assertEqual(negative['terminal'], demo(30002, alive=False)['terminal'])
        self.assertNotIn('samples', negative)
        self.assertEqual((out / 'frozen-manifest.json').read_bytes(), (directory / 'manifest.json').read_bytes())
        (directory / 'normal-30001.json.gz').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'drift'):
            recheck(directory, md['inputFiles'])
        with self.assertRaises(ValueError):
            C.new_output(out)

    def test_orphan_raw_cannot_be_called_unrun(self):
        directory, _ = fixture(self.base)
        orphan = directory / 'normal-30003.json.gz'
        orphan.write_bytes(gzip.compress(b'{}\n'))
        with self.assertRaisesRegex(ValueError, 'Unregistered or missing raw artifacts'):
            admit(directory, C.sha(directory / 'manifest.json'), GAME, POOL,
                  Evidence(C.new_output(self.base / 'orphan-rejected')))
        orphan.unlink()  # This test's own temporary file only.
        _, metadata = admit(directory, C.sha(directory / 'manifest.json'), GAME, POOL,
                            Evidence(C.new_output(self.base / 'before-late-orphan')))
        orphan.write_bytes(gzip.compress(b'{}\n'))
        with self.assertRaisesRegex(ValueError, 'file set changed'):
            recheck(directory, metadata['inputFiles'])

    def test_frozen_rejects_running_partial_plan_source_and_hash(self):
        directory, original = fixture(self.base)
        edits = [lambda m: m.update(status='running'), lambda m: m.update(status='failed'), lambda m: m.pop('finishedAt'),
                 lambda m: m['games'].pop(), lambda m: m['games'][0].update(seed=90001),
                 lambda m: m.update(status='complete'),
                 lambda m: m['sourceSHA256'].update({'/bad/source.py': '0' * 64}),
                 lambda m: m['sourceSHA256'].update({'/synthetic/preview-tools/' + PINS['collector']['name']: '0' * 64})]
        path = directory / 'manifest.json'
        for edit in edits:
            m = copy.deepcopy(original)
            edit(m)
            path.write_bytes(C.encoded(m))
            with self.assertRaises((ValueError, KeyError)):
                frozen(path, C.sha(path))
        with self.assertRaisesRegex(ValueError, 'SHA'):
            frozen(path, '0' * 64)

    def test_gzip_crc_truncation_tail_multiple_json_and_size(self):
        directory, m = fixture(self.base)
        row = m['games'][0]
        path = directory / row['artifact']
        original = path.read_bytes()
        variants = [original[:-4], original + b'bad-tail', original + gzip.compress(b'{}\n'),
                    gzip.compress(b'{"x":1,"x":2}\n'), gzip.compress(b'{"x":NaN}\n'),
                    gzip.compress(b'{}')]
        for value in variants:
            path.write_bytes(value)
            row.update(bytes=len(value), artifactSHA256=C.sha(path))
            with self.assertRaises((ValueError, OSError, EOFError)):
                read_artifact(path, row)
        path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'SHA/bytes'):
            read_artifact(path, row)
        with self.assertRaises(ValueError):
            Evidence(C.new_output(self.base / 'small'), limit=1).put('x', b'xx')

    def test_native_validation_shapes_and_hidden_round_are_auditable(self):
        directory, m = fixture(self.base)
        row = m['games'][0]
        d = demo()
        t = d['terminal']
        rejection = dict(method='pickReward', diagnostic=True, reason='pure-validation', result={'error': 'HAND_FULL'})
        audit = t['partnerAudit']['partner-1']
        audit.update(rejected=1, rejections=[rejection])
        t['partnerActionErrors'] = t['episode']['diagnostics']['partnerActionErrors'] = 1
        before = copy.deepcopy(d)
        self.assertTrue(complete_contract(d, row, GAME, POOL))
        self.assertEqual(d, before)  # Never zero counters or normalize the saved raw response.
        rejection['result']['ok'] = True
        with self.assertRaisesRegex(ValueError, 'Non-diagnostic'):
            complete_contract(d, row, GAME, POOL)
        rejection['result']['ok'] = False
        self.assertTrue(complete_contract(d, row, GAME, POOL))
        rejection['diagnostic'] = False
        with self.assertRaisesRegex(ValueError, 'Non-diagnostic'):
            complete_contract(d, row, GAME, POOL)
        hidden = demo()
        hidden['terminal']['rounds'] = hidden['terminal']['summary']['roundsPassed'] = 15
        for p in hidden['terminal']['summary']['players']:
            p['roundsPassed'] = 15
        hidden['samples'][-1]['round'] = 15
        hidden_row = {**row, 'rounds': 15, 'learnerRounds': 15, 'eligiblePositive': False}
        self.assertFalse(complete_contract(hidden, hidden_row, GAME, POOL))

    def test_semantics_full_terminal_row_and_labels(self):
        directory, m = fixture(self.base)
        row, original = m['games'][0], demo()
        self.assertTrue(complete_contract(original, row, GAME, POOL))
        edits = [lambda d: d['terminal']['summary'].update(errors=1),
                 lambda d: d['terminal']['summary']['players'][0].update(seat=1),
                 lambda d: d['terminal']['episode']['partners'].update(split='eval'),
                 lambda d: d['terminal'].update(engineErrors=1),
                 lambda d: d['terminal'].update(controllerErrors=1),
                 lambda d: d['terminal'].update(partnerActionErrors=1),
                 lambda d: d['terminal'].update(steps=3),
                 lambda d: d['terminal']['summary']['players'][0].pop('lineup'),
                 lambda d: d['terminal']['summary']['players'][0].update(alive=False),
                 lambda d: d['terminal']['observation']['own'].update(alive=False),
                 lambda d: d['terminal']['observation']['own'].update(lp=999),
                 lambda d: d['terminal']['summary']['players'][1].update(alive=False, victory=False, eliminatedRound=999),
                 lambda d: d.update(rejectedLabels=1), lambda d: d.update(teacher='other'),
                 lambda d: d['samples'][0].update(action=True),
                 lambda d: d['samples'][0]['features']['mask'].__setitem__(0, False),
                 lambda d: d['samples'][0].update(round=0),
                 lambda d: d['samples'][0]['features'].update(futureVictory=True)]
        for edit in edits:
            d = copy.deepcopy(original)
            edit(d)
            with self.assertRaises((ValueError, KeyError)):
                complete_contract(d, row, GAME, POOL)
        for key in ('samples', 'rounds', 'learnerRounds', 'eligiblePositive', 'teamVictory'):
            r = dict(row)
            r[key] = None
            with self.assertRaises(ValueError):
                complete_contract(original, r, GAME, POOL)
        # A team carry remains valid audit data, but cannot train as a positive.
        self.assertFalse(complete_contract(demo(30002, alive=False), m['games'][2], GAME, POOL))
        rejected = copy.deepcopy(original)
        rejected['rejectedLabels'] = 1
        self.assertFalse(complete_contract(rejected, {**row, 'rejectedLabels': 1, 'eligiblePositive': False}, GAME, POOL))

    def test_actual_original_bc_checkpoint_and_fixed_eval_driver(self):
        directory, _ = fixture(self.base)
        out = C.new_output(self.base / 'trained')
        evidence = Evidence(out)
        samples, md = admit(directory, C.sha(directory / 'manifest.json'), GAME, POOL, evidence)
        parent = self.base / 'experiments/bc-expansion-v1/all.pt'
        self.L.save_checkpoint(parent, self.L.ActorCritic(90, 15), SCHEMA, C.RULES, {})
        check_parent(self.base, parent, C.sha(parent))
        with self.assertRaises(ValueError):
            check_parent(self.base, parent, '0' * 64)
        source = source_identity(CODE)
        report = train(self.L, parent, samples, md, out, source, GAME, evidence)
        C.write(out / 'train.json', report)
        self.assertEqual(report['result']['actualUpdates'], 200)
        self.assertEqual(report['result']['actualDraws'], 6800)
        self.assertTrue(report['result']['criticHeadUnchanged'])
        self.assertNotEqual(report['result']['initialParameterSHA256'], report['result']['parameterSHA256'])
        self.assertEqual(report['trainingEpisodeGroups'], [30001])
        selection = C.parse((out / 'selection.json').read_bytes())
        self.assertEqual(selection['groups'], [dict(seed=30001, eligibleLabels=4, distinctDrawnLabels=4, draws=6800)])
        evout = C.new_output(self.base / 'eval')
        rows = schedule()
        def fake_job(row, *args):
            return {**row, 'status': 'failed' if row['id'] == 'parent-original-01' else 'complete',
                    'teamVictory': True, 'learnerIndividualVictory': False}
        teams_before = copy.deepcopy(E.TEAMS)
        with patch.object(E, 'game_job', side_effect=fake_job) as job, patch('evaluation.pool_sha', return_value=POOL):
            evaluate(self.L, out, parent, CODE, self.base, GAME, evout, source, rows,
                     time.monotonic() + 3, lambda: None)
            self.assertEqual(job.call_count, 64)
        self.assertEqual(E.TEAMS, teams_before)
        result = summary(rows)
        self.assertEqual((result['denominator'], result['failed'], result['teamWins'], result['learnerWins']), (64, 1, 63, 0))
        self.assertTrue(all(c['denominator'] == 16 for c in result['cells'].values()))
        self.assertEqual(summary(schedule())['unrun'], 64)
        self.assertEqual({r['seed'] for r in rows}, set(range(1, 17)))
        with patch.object(E, 'game_job') as job:
            # Tampered training provenance must fail before any game starts.
            (out / 'selection.json').write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                evaluate(self.L, out, parent, CODE, self.base, GAME, C.new_output(self.base / 'reject'), source,
                         schedule(), time.monotonic() + 1, lambda: None)
            job.assert_not_called()

    def test_original_game_job_retains_terminal_and_eof_failure(self):
        raw = C.encoded(dict(ok=True, **terminal(seed=1, split='eval', steps=0)))
        class FakeProtocol:
            def __init__(self, command, env, out, deadline, byte_limit):
                self.bytes = 0
            def request(self, command):
                return C.parse(raw), raw
            def charge(self, n):
                self.bytes += n
            def finish(self):
                raise ValueError('Extra/truncated data after close acknowledgement')
            def close(self):
                pass
        out = C.new_output(self.base / 'protocol')
        with patch.object(E, 'Protocol', FakeProtocol):
            result = E.game_job(schedule()[0], None, dict(schema=SCHEMA), CODE, self.base, out,
                                time.monotonic() + 1, MIB, POOL)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('close acknowledgement', result['error'])
        self.assertFalse(result['eof'])
        self.assertEqual((out / result['id'] / 'terminal.json').read_bytes(), raw)

    def test_pins_and_python310_syntax(self):
        source_identity(CODE)
        pin = C.parse((CODE / 'policy/pins/rules-manifest.json').read_bytes())
        self.assertEqual(len(pin['files']), 486)
        for p in HERE.glob('*.py'):
            ast.parse(p.read_text(), feature_version=(3, 10))
        with patch.object(C, 'game_identity', return_value=('synthetic', GAME)) as primitive:
            self.assertEqual(game_identity(self.base, CODE), ('synthetic', GAME))
            primitive.assert_called_once()
        with patch.object(C, 'sha', return_value='0' * 64):
            with self.assertRaisesRegex(ValueError, 'Source guard'):
                source_identity(CODE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
