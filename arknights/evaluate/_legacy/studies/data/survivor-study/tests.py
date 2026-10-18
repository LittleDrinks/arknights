"""Synthetic contracts only: no production inputs, static game loaders or real games."""
import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from common import (ARMS, CONFIG, RULES, TEAMS, code_root, digest, encoded, learner, new_output,
                    parse, sha, source_identity, terminal_contract, write)
from data import admit, read_gzip, select68
import evaluate as E
from train import train

SCHEMA = dict(version='shop-v1', ids=[], bonds=[], bands=[])
GAME = dict(schema=SCHEMA, rules=RULES, dataSHA256='d' * 64)
SOURCE = {'originalFiles': {'rl/features.mjs': 'f' * 64}, 'SHA256': 's' * 64}


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def terminal(seed=50003, steps=68):
    audits = {f'partner-{i}': dict(rejected=0, rejections=[]) for i in (1, 2, 3)}
    diagnostics = dict(partnerAudit=audits, partnerActionErrors=0, controllerDiagnosticErrors=0,
                       rejectedActions=[], controllerBlockedMoves=0, controllerBlockedMoveReasons=[], filteredMoves=[])
    return dict(done=True, schema=SCHEMA, rules=RULES, steps=steps, rounds=14, reward=1, victory=True,
                episode=dict(seed=seed, mode='coop', difficulty='ABYSS', budget=128, diagnostics=diagnostics),
                summary=dict(seed=seed, modeId='mode_multi_abyss', difficulty='ABYSS', victory=True, errors=0,
                             roundsPassed=14, reason='victory', players=[
                                 dict(playerId='learner' if i == 0 else f'partner-{i}', seat=i,
                                      victory=i in (1, 2), alive=i in (1, 2), roundsPassed=14 if i in (1, 2) else 10,
                                      eliminatedRound=None if i in (1, 2) else 11, lp=1 if i in (1, 2) else 0,
                                      lineup=[], bonds=[], stats={})
                                 for i in range(4)]),
                observation=dict(public=dict(phase='RESULT'), own=dict(playerId='learner', seat=0, alive=False, lp=0)),
                features=dict(global_=[]), candidates=[], engineErrors=0, actionErrors=0, controllerErrors=0, **diagnostics)


def fixture(base):
    input_dir = base / 'input'
    input_dir.mkdir()
    t = terminal()
    t['features'] = {'global': [0] * 90, 'candidates': [], 'mask': []}
    samples, labels, dispositions = [], [], []
    for seq in range(1066):
        positive = 1 <= seq <= 207
        primary = 300 <= seq < 368
        seat = 1 if seq <= 93 else 2
        d = dict(seq=seq, parentSeq=None, playerId=f'partner-{seat}' if positive else 'learner',
                 source='partner-controller' if positive else 'policy' if primary else 'engine',
                 method='setReady', action={'t': 'g.ready', 'ready': True}, accepted=True, result={'ok': True},
                 selection='positive-label' if positive else 'excluded-seat' if primary else 'non-shop-method')
        dispositions.append(d)
        if not positive:
            continue
        i, round_ = len(samples), (seq - 1) % 14 + 1
        f = {'global': [round_ / 20] + [0] * 89, 'candidates': [[0] * 15, [1] * 15], 'mask': [True, True]}
        s = {'features': f, 'action': i % 2}
        cs = [{'type': 'ready'}, {'type': 'refresh'}]
        samples.append(s)
        labels.append(dict(sample=i, seq=seq, parentSeq=None, playerId=d['playerId'], seat=seat, round=round_,
                           source=d['source'], method=d['method'], rawAction=d['action'], candidateIndex=s['action'],
                           candidate=cs[s['action']], candidates=cs, candidateSHA256=digest(js(cs)), sampleSHA256=digest(js(s)),
                           counterMode='observable-teammate-teacher', prepSteps=0, prepBudget=128))
    primary = base / 'primary.jsonl.gz'
    primary.write_bytes(gzip.compress(b'\n'.join(js(s) for s in samples[:68]) + b'\n'))
    _, _, reference = read_gzip(primary)
    provenance = {'rules': RULES, 'trace': {'sha256': 'a' * 64}, 'episode': t['episode']}
    seats = {p['playerId']: dict(seat=p['seat'], individualVictory=p['victory'], teamVictory=True,
                               alive=p['alive'], roundsPassed=p['roundsPassed'], eliminatedRound=p['eliminatedRound'],
                               positive=p['victory'], samples={0: 0, 1: 93, 2: 114, 3: 0}[p['seat']])
             for p in t['summary']['players']}
    header = dict(type='episode', schema='arknights.bot-transitions.v1', config='coop:ABYSS', seed=50003,
                  players=4, commit=RULES, rehearsal=0, policySteps=68, engineErrors=0, schedulerErrors=0,
                  summary=t['summary'], provenance=provenance, victory=True, roundsPassed=14, terminalReason='victory', methodCalls=1066)
    m = dict(format='winning-labels/v1', rules=RULES, schema=SCHEMA, staticData=GAME,
             mode='coop', difficulty='ABYSS', samples=207, independentEpisodes=1, positiveSeatSequences=2,
             episodeGroup='a' * 64, traceSHA256='a' * 64, provenance=provenance, sources=SOURCE['originalFiles'],
             implementationSources={'extract.mjs': 'e' * 64}, limitations=['synthetic'], raw={'sha256': 'b' * 64},
             reference=reference, terminal=t, header=header, seats=seats,
             primaryValidation=dict(exact=True, samples=68, primary=[dict(seq=300 + i, traceIndex=i) for i in range(68)]))
    contents = {'samples.jsonl.gz': samples, 'labels.jsonl.gz': labels, 'dispositions.jsonl.gz': dispositions, 'metadata.json.gz': [m]}
    from collections import Counter
    audit = dict(ok=True, primaryExact=68, samples=207, independentEpisodes=1, seats=seats, transitions=1066, rejected=0,
                 dispositionCounts=dict(Counter(d['selection'] for d in dispositions)), rawSHA256='b' * 64,
                 referenceSHA256=reference['sha256'], files={})
    for name, rows in contents.items():
        path = input_dir / name
        path.write_bytes(gzip.compress(b'\n'.join(js(r) for r in rows) + b'\n'))
        audit['files'][name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    write(input_dir / 'audit.json', audit)
    return input_dir, primary


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def admitted(self):
        input_dir, primary = fixture(self.base)
        return admit(input_dir, primary, source=SOURCE, game=GAME), input_dir, primary

    def rewrite(self, input_dir, name, edit):
        rows, _, _ = read_gzip(input_dir / name)
        edit(rows)
        p = input_dir / name
        p.write_bytes(gzip.compress(b'\n'.join(js(r) for r in rows) + b'\n'))
        audit = parse((input_dir / 'audit.json').read_bytes())
        audit['files'][name] = {'sha256': sha(p), 'bytes': p.stat().st_size}
        write(input_dir / 'audit.json', audit)

    def test_admission_selection_and_no_future_fields(self):
        data, _, _ = self.admitted()
        selected, strata = select68(data['labels'])
        self.assertEqual(selected, data['selections']['survivor68'])
        self.assertEqual(len(set(selected)), 68)
        self.assertEqual(sum(len(s['selected']) for s in strata), 68)
        self.assertEqual(data['selections']['survivor207'], list(range(207)))
        # Outcomes/candidate quality have no effect on selection.
        altered = [{**l, 'candidateIndex': 0, 'futureVictory': False} for l in data['labels']]
        self.assertEqual(select68(altered), (selected, strata))
        self.assertEqual(data['inputReviewStatus'], 'interface-checked-independent-review-record-external')

    def test_bad_hash_and_full_eof(self):
        _, input_dir, primary = self.admitted()
        p = input_dir / 'samples.jsonl.gz'
        p.write_bytes(p.read_bytes() + b'garbage')
        with self.assertRaisesRegex(ValueError, 'SHA/size'):
            admit(input_dir, primary, source=SOURCE, game=GAME)
        with self.assertRaises((OSError, EOFError)):
            read_gzip(p)
        p.write_bytes(gzip.compress(b'{"x":1}\n')[:-5])
        with self.assertRaises(EOFError):
            read_gzip(p)
        p.write_bytes(gzip.compress(b'{"x":1}\n') + gzip.compress(b'{"x":2}\n'))
        self.assertEqual(len(read_gzip(p)[0]), 2)

    def test_semantic_rejections_with_matching_audit(self):
        _, input_dir, primary = self.admitted()
        original = {p.name: p.read_bytes() for p in input_dir.iterdir()}
        changes = [
            ('labels.jsonl.gz', lambda r: r[0].update(round=15)),
            ('labels.jsonl.gz', lambda r: r[0].update(seat=0)),
            ('labels.jsonl.gz', lambda r: r[0].update(sampleSHA256='0' * 64)),
            ('samples.jsonl.gz', lambda r: r[0].update(terminal={'victory': True})),
            ('samples.jsonl.gz', lambda r: r.pop()),
            ('metadata.json.gz', lambda r: r[0]['terminal']['summary'].update(errors=1)),
            ('metadata.json.gz', lambda r: r[0]['terminal']['summary']['players'].pop()),
            ('metadata.json.gz', lambda r: r[0]['reference'].update(sha256='0' * 64)),
            ('metadata.json.gz', lambda r: r[0]['staticData'].update(dataSHA256='0' * 64)),
            ('metadata.json.gz', lambda r: r[0].update(episodeGroup='0' * 64)),
            ('dispositions.jsonl.gz', lambda r: r.pop()),
        ]
        for name, change in changes:
            with self.subTest(name=name):
                for n, b in original.items():
                    (input_dir / n).write_bytes(b)
                self.rewrite(input_dir, name, change)
                with self.assertRaises((ValueError, KeyError)):
                    admit(input_dir, primary, source=SOURCE, game=GAME)

    def test_nooverwrite_and_sourceguard(self):
        out = new_output(self.base / 'out')
        with self.assertRaises(ValueError):
            new_output(out)
        link = self.base / 'link'
        link.symlink_to(self.base / 'missing')
        with self.assertRaises(ValueError):
            new_output(link)
        code = Path(__file__).resolve().parents[1]
        self.assertEqual(source_identity(code)['base'], '23bdfc3051a5059b41c3ffb7dcb9ddf421b3a697')
        with self.assertRaises(FileNotFoundError):
            source_identity(self.base)
        nested = self.base / 'experiment-code'
        (nested / 'rl').mkdir(parents=True)
        (nested / 'coop').mkdir()
        (nested / 'rl/learner.py').touch()
        (nested / 'coop/env.mjs').touch()
        self.assertEqual(code_root(self.base), nested)

    def test_original_bc_200_updates_per_arm_and_roundtrip(self):
        admitted, _, _ = self.admitted()
        L = learner(Path(__file__).resolve().parents[1])
        L.torch.manual_seed(9)
        model = L.ActorCritic(90, 15, 4)
        parent = self.base / 'all.pt'
        L.save_checkpoint(parent, model, SCHEMA, RULES, {'synthetic': True})
        before = sha(parent)
        out = new_output(self.base / 'train')
        report = train(L, parent, admitted, out, SOURCE, GAME)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(sha(parent), before)
        self.assertEqual(len({a['orderSHA256'] for a in report['arms'].values()}), 1)
        for name in ARMS:
            a = report['arms'][name]
            self.assertEqual((a['actualUpdates'], a['actualDraws']), (200, 6800))
            self.assertEqual(a['initialParameterSHA256'], L.digest(model))
            restored, ckpt = L.load_checkpoint(out / (name + '.pt'))
            self.assertEqual(ckpt['metadata']['episodeGroup'], 'a' * 64)
            self.assertTrue(L.torch.equal(restored.critic.weight, model.critic.weight))
            self.assertNotEqual(L.digest(restored), L.digest(model))
        # Exercise the actual posttrain gate/scheduler/report using synthetic jobs only.
        def job(row, *args):
            return {**row, 'status': 'failed' if row['seed'] == 2 else 'complete',
                    'teamVictory': True, 'learnerIndividualVictory': False}
        with patch.object(E, 'game_job', side_effect=job), patch.object(E, 'source_identity', return_value=SOURCE), \
             patch.object(E, 'game_identity', return_value=(self.base, GAME)):
            evout = new_output(self.base / 'eval')
            result = E.evaluate(L, out, parent, Path(__file__).resolve().parents[1], self.base, GAME, evout, SOURCE,
                                workers=4, timeout=1, total_timeout=2, byte_limit=1000000)
        self.assertEqual((result['status'], result['denominator'], result['complete'], result['failed']), ('failed', 128, 120, 8))
        self.assertEqual(result['teamWins'], 120)
        self.assertEqual({r['status'] for r in parse((evout / 'manifest.json').read_bytes())['games']}, {'unrun'})
        with patch.object(E, 'game_job') as jobs:
            with self.assertRaisesRegex(ValueError, 'source/config'):
                E.evaluate(L, out, parent, self.base, self.base, GAME, new_output(self.base / 'drift'), {},
                           workers=1, timeout=1, total_timeout=1, byte_limit=1000000)
            jobs.assert_not_called()

    def test_schedule_denominator(self):
        rows = E.schedule()
        self.assertEqual(len(rows), 128)
        self.assertEqual({r['seed'] for r in rows}, set(range(1, 17)))
        rows[0].update(status='complete', teamVictory=True, learnerIndividualVictory=False)
        rows[1].update(status='failed', teamVictory=True)
        s = E.summary(rows)
        self.assertEqual((s['denominator'], s['complete'], s['failed'], s['unrun']), (128, 1, 1, 126))
        self.assertEqual((s['teamWinsPerScheduled'], s['learnerWinsPerScheduled']), (1 / 128, 0))
        self.assertTrue(all(c['denominator'] == 16 for c in s['cells'].values()))
        out = new_output(self.base / 'rejected-eval')
        result = E.run_evaluation(None, self.base / 'missing', None, None, None, None, out, None,
                                  workers=1, timeout=1, total_timeout=1, byte_limit=1000)
        self.assertEqual((result['status'], result['denominator'], result['unrun']), ('failed', 128, 128))
        self.assertTrue(parse((out / 'manifest.json').read_bytes())['admissionFailed'])

    def eval_fixture(self):
        t = terminal(seed=1, steps=1)
        t['ok'] = True
        t['features'] = {'global': [0] * 90, 'candidates': [], 'mask': []}
        t['episode']['partners'] = dict(pool='normal', split='eval', poolSeed=313, fixed=TEAMS['original'],
                                       manifest='coop-heuristics-v1', manifestSHA256='p',
                                       seats=[dict(seat=i, id='original-v1', family='upstream-bot') for i in (1, 2, 3)])
        state = copy.deepcopy(t)
        state.update(done=False, steps=0, summary=None, reward=0, victory=False,
                     candidates=[{'type': 'ready'}], features={'global': [0] * 90, 'candidates': [[0] * 15], 'mask': [True]})
        del state['episode']['diagnostics']
        del state['partnerAudit']
        return state, t

    def test_terminal_errors_and_individual_outcome(self):
        _, t = self.eval_fixture()
        self.assertFalse(terminal_contract(t, seed=1, steps=1, schema=SCHEMA)['victory'])
        for change in [lambda t: t.update(engineErrors=1), lambda t: t.update(fatalRejections=1),
                       lambda t: t.update(controllerErrors=1), lambda t: t['summary'].update(seed=2),
                       lambda t: t['summary']['players'][0].update(seat=1), lambda t: t.update(steps=2)]:
            bad = copy.deepcopy(t)
            change(bad)
            with self.assertRaises(ValueError):
                terminal_contract(bad, seed=1, steps=1, schema=SCHEMA)
        known = copy.deepcopy(t)
        rejection = dict(diagnostic=True, reason='pure-validation', result={'ok': False, 'error': 'BAD_TILE'})
        known.update(controllerErrors=1, controllerDiagnosticErrors=1, rejectedActions=[rejection])
        known['episode']['diagnostics'].update(controllerDiagnosticErrors=1, rejectedActions=[rejection])
        terminal_contract(known, seed=1, steps=1, schema=SCHEMA)
        rejection['reason'] = 'state-changed'
        with self.assertRaisesRegex(ValueError, 'Non-diagnostic'):
            terminal_contract(known, seed=1, steps=1, schema=SCHEMA)

    def test_protocol_complete_bad_eof_errors_and_budget(self):
        state, t = self.eval_fixture()
        script = self.base / 'fake.py'
        script.write_text('import json,sys\n'
                          'states=json.load(open(sys.argv[1])); mode=sys.argv[2]\n'
                          'for line in sys.stdin:\n'
                          ' c=json.loads(line)["cmd"]\n'
                          ' if c=="close":\n'
                          '  print(json.dumps({"ok":True}),flush=True)\n'
                          '  if mode=="tail": print("unexpected",flush=True)\n'
                          '  if mode=="stderr": print("fatal",file=sys.stderr,flush=True)\n'
                          '  break\n'
                          ' print(json.dumps(states[0 if c=="reset" else 1]),flush=True)\n')
        fixture_path = self.base / 'states.json'
        fixture_path.write_bytes(encoded([state, t]))
        protocol = E.Protocol
        class Model:
            def act(self, f, greedy):
                assert greedy and set(f) == {'global', 'candidates', 'mask'}
                return 0, 0, 0
        for mode, cap in [('good', 1_000_000), ('tail', 1_000_000), ('stderr', 1_000_000), ('budget', 100)]:
            with self.subTest(mode=mode):
                out = new_output(self.base / mode)
                def factory(command, env, directory, deadline, byte_limit):
                    return protocol([sys.executable, '-B', str(script), str(fixture_path), mode], env, directory, deadline, byte_limit)
                with patch.object(E, 'Protocol', factory):
                    result = E.game_job(E.schedule()[0], Model(), {'schema': SCHEMA}, self.base, self.base, out,
                                        time.monotonic() + 2, cap, 'p')
                self.assertEqual(result['status'], 'complete' if mode == 'good' else 'failed')
                if mode == 'good':
                    directory = out / result['id']
                    self.assertEqual(parse((directory / 'terminal.json').read_bytes()), t)
                    action = parse((directory / 'actions.jsonl').read_bytes())
                    self.assertEqual(action['candidates'], state['candidates'])
                    self.assertFalse(result['learnerIndividualVictory'])
                    self.assertTrue(result['teamVictory'])

    def test_protocol_timeout_and_strict_json(self):
        out = new_output(self.base / 'timeout')
        channel = E.Protocol([sys.executable, '-c', 'import time; time.sleep(5)'], {}, out, time.monotonic() + .05, 1000)
        try:
            with self.assertRaises(TimeoutError):
                channel.request({'cmd': 'reset'})
        finally:
            channel.close()
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1}\n{}'):
            with self.assertRaises(ValueError):
                parse(data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
