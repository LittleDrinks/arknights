"""Synthetic contracts only. No real corpus, static game loader, collection or evaluation."""
import ast
from contextlib import redirect_stdout
import copy
import gzip
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from core import (A, C, N, R, S, CODE, HERE, CONFIG, MIB, OLD_SHA, REFERENCE_REPORT_SHA,
                  PARENT_SHA, Evidence, file_id, inventory, read, require, safe, source_identity, load_fit)
from admission_scale import (header, new_admit, receipt, reorder_old, arm_labels, exact)
from train_scale import original_train, parameter_shas, save_standard, counts
from run import disjoint, parser, Budgets, main

spec = __import__('importlib.util', fromlist=['']).spec_from_file_location('scale_tiny_fixture', CODE/'teacher-scale/fixture.py')
fixture = __import__('importlib.util', fromlist=['']).module_from_spec(spec)
spec.loader.exec_module(fixture)
GAME = fixture.GAME
POOL = N.pool_sha(CODE)


def demo(seed, pool='normal', alive=True, rejected=0):
    data = fixture.demo(seed, pool, alive, rejected)
    data['terminal']['episode']['partners']['manifestSHA256'] = POOL
    return data


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['SCALE_TEST_TMP'])
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(C.encoded(value))
        return file_id(path)

    def batch(self, alter=None, lie=False):
        """Fabricated tiny raw payloads on the exact real 8192 schedule, never a collector."""
        out = self.base/'synthetic'
        out.mkdir()
        plan = S.plan()
        rows = [{k: r[k] for k in ('index', 'seed', 'pool', 'lane')} | {'status': 'not-run'} for r in plan]
        self.write(out/'plan.json', dict(games=plan, planned=8192, split='train'))
        source = S.source_identity()
        for filename in ('source-before.json', 'source-after.json'):
            self.write(out/filename, dict(source=source, game=GAME, poolSHA256=POOL))
        members = {}
        for i in range(4):
            r = rows[i]
            base = {k: r[k] for k in ('index', 'seed', 'pool', 'lane')}
            stem = f"{i:04}-{r['pool']}-{r['seed']}"
            started = dict(**base, status='started', startedAt='synthetic-start', finishedAt=None,
                           stdoutStart=0, stderrStart=0)
            start_id = self.write(out/'journals'/(stem+'.started.json'), started)
            data = demo(r['seed'], r['pool'], alive=i != 3)
            fields = S.outcome(data, r, GAME, POOL)
            if lie and i == 3:
                fields['eligiblePositive'] = True
            if alter:
                alter(i, data)
            raw = C.encoded(data)
            path = out/'cases'/(stem+'.json.gz')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(gzip.compress(raw, mtime=0))
            artifact = dict(**file_id(path), payloadBytes=len(raw), payloadSHA256=C.digest(raw), jsonNewline=True, gzipEOF=True)
            r.update(started, **fields)
            r.update(status='complete', finishedAt='synthetic-end', processEOF=False, jsonEOF=True,
                     startedIdentity=start_id, stdoutEnd=len(raw), stderrEnd=0, wireBytes=len(raw),
                     artifact=str(path.relative_to(out)), artifactIdentity=artifact)
            self.write(out/'journals'/(stem+'.completed.json'), r)
            members[r['lane']] = (r, raw)
        lanes = {}
        for lane in sorted({r['lane'] for r in plan}):
            directory = out/'lanes'/lane
            start = dict(lane=lane, status='started', startedAt='synthetic-start', finishedAt=None)
            start_id = self.write(directory/'started.json', start)
            spawned = lane in members
            size, files = 0, {}
            if spawned:
                row, raw = members[lane]
                wire = raw+b'{"ok":true}\n'
                (directory/'stdout.gz').write_bytes(gzip.compress(wire, mtime=0))
                (directory/'stderr.log').write_bytes(b'')
                action = dict(index=row['index'], seed=row['seed'], pool=row['pool'],
                              command=dict(cmd='demo', seed=row['seed'], mode='coop', difficulty='ABYSS', budget=128))
                (directory/'actions.jsonl').write_bytes(C.encoded(action)+C.encoded(dict(command={'cmd': 'close'})))
                size = len(wire)
                files = {n: file_id(directory/n) for n in ('stdout.gz', 'stderr.log', 'actions.jsonl')}
            done = dict(**{k: v for k, v in start.items() if k not in ('status', 'finishedAt')},
                        status='complete', finishedAt='synthetic-end', startedIdentity=start_id,
                        spawned=spawned, errors=[], closeACKAndProcessEOF=spawned,
                        wireBytes=size, stdoutBytes=size, stderrBytes=0, files=files)
            self.write(directory/'completed.json', done)
            lanes[lane] = done
        files = inventory(out, time.monotonic()+5)['files']
        m = dict(kind='natural-teacher-scale/v1', status='incomplete', frozen=True, globalFailed=False,
                 globalErrors=[], trainingAllowed=False, finishedAt='synthetic-end', planned=8192,
                 pairedTrainingSeeds=4096, games=rows, lanes=lanes, files=files, filesSHA256=C.digest(C.encoded(files)))
        self.write(out/'manifest.json', m)
        return out, m, source

    def admit(self, out, source):
        return new_admit(out, C.sha(out/'manifest.json'), GAME, source, time.monotonic()+5)

    def test_exact8192_inventory_all_pools_groups(self):
        out, m, source = self.batch()
        samples, md, _ = self.admit(out, source)
        self.assertEqual(len(samples), 6)
        self.assertEqual(md['statusCounts'], {'complete': 4, 'not-run': 8188})
        self.assertEqual({r['pool'] for r in md['labels']}, {'normal', 'mixed'})
        self.assertEqual({r['episodeGroup'] for r in md['labels']}, {32001, 32002})
        self.assertTrue(all(set(s) == {'features', 'action'} for s in samples))
        self.assertFalse(m['games'][3]['eligiblePositive'])
        (out/'unknown.log').write_bytes(b'not a label')
        with self.assertRaises(ValueError):
            self.admit(out, source)

    def test_running_unfrozen_failed_and_false_complete(self):
        out, original, _ = self.batch()
        variants = [dict(status='running'), dict(frozen=False), dict(globalFailed=True),
                    dict(globalErrors=['lane failed']), dict(status='complete'), dict(globalFailed=0),
                    dict(planned=True), dict(pairedTrainingSeeds=4095)]
        for fields in variants:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                header({**original, **fields})
        for status in ('failed', 'interrupted', 'unknown', 'started'):
            m = copy.deepcopy(original)
            m['games'][-1]['status'] = status
            with self.assertRaises(ValueError):
                header(m)
        original['games'][-1]['seed'] = 90017
        with self.assertRaises(ValueError):
            header(original)

    def test_complete_header_requires_all8192_disk_journals(self):
        out, m, source = self.batch()
        for row in m['games']:
            row['status'] = 'complete'
        m['status'] = 'complete'
        header(m)
        self.write(out/'manifest.json', m)
        with self.assertRaises(ValueError):
            self.admit(out, source)

    def test_missing_positive_raw_and_complete_journal(self):
        out, m, source = self.batch()
        row = m['games'][0]
        path = out/row['artifact']
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            self.admit(out, source)
        path.write_bytes(original)
        (out/'journals/0000-normal-32001.completed.json').unlink()
        with self.assertRaises(ValueError):
            self.admit(out, source)

    def test_false_positive_with_consistent_artifact_hashes(self):
        out, _, source = self.batch(lie=True)
        with self.assertRaisesRegex(ValueError, 'eligiblePositive'):
            self.admit(out, source)

    def test_full_terminal_semantics_even_negative_raw(self):
        def change(i, data):
            if i == 3:
                del data['terminal']['episode']['diagnostics']
        out, _, source = self.batch(alter=change)
        with self.assertRaises(ValueError):
            self.admit(out, source)

    def test_invalid_sample_on_negative_row_sha_consistent(self):
        def change(i, data):
            if i == 3:
                data['samples'][0]['features']['privateFuture'] = [42]
        out, _, source = self.batch(alter=change)
        with self.assertRaises(ValueError):
            self.admit(out, source)

    def test_source_identity_not_only_equality_before_after(self):
        out, _, source = self.batch()
        fake = dict(source=source, game={**GAME, 'dataSHA256': 'e'*64}, poolSHA256=POOL)
        for name in ('source-before.json', 'source-after.json'):
            self.write(out/name, fake)
        with self.assertRaisesRegex(ValueError, 'source/game/pool'):
            self.admit(out, source)
        source_identity()
        with patch('core.PINS', {**__import__('core').PINS, 'files': {'rl/learner.py': '0'*64}}):
            with self.assertRaisesRegex(ValueError, 'Dependency drift'):
                source_identity()

    def test_gzip_eof_newline_fulljson_and_manifest_sha(self):
        path = self.base/'raw.gz'
        raw = C.encoded(dict(ok=True))
        packed = gzip.compress(raw, mtime=0)
        for data in (packed[:-1], packed+b'bad', gzip.compress(raw.rstrip(), mtime=0)):
            path.write_bytes(data)
            with self.assertRaises((ValueError, EOFError, OSError)):
                R.payload_identity(path, 1000, time.monotonic()+1, retain=True)
        path.write_bytes(gzip.compress(raw+raw, mtime=0))
        _, payload = R.payload_identity(path, 1000, time.monotonic()+1, retain=True)
        with self.assertRaises(ValueError):
            C.parse(payload)
        out, _, source = self.batch()
        with self.assertRaisesRegex(ValueError, 'SHA'):
            new_admit(out, '0'*64, GAME, source, time.monotonic()+5)

    def test_symlink_special_files_output_collisions_and_replacement(self):
        out, _, source = self.batch()
        (out/'extra').symlink_to('/nonexistent')
        with self.assertRaises(ValueError):
            self.admit(out, source)
        (out/'extra').unlink()
        os.mkfifo(out/'extra')
        with self.assertRaises(ValueError):
            inventory(out, time.monotonic()+1)
        (out/'extra').unlink()
        link = self.base/'link'
        link.symlink_to(out, target_is_directory=True)
        with self.assertRaises(ValueError):
            safe(link/'plan.json')
        with self.assertRaises(ValueError):
            safe(link/'..'/'elsewhere')
        with self.assertRaises(ValueError):
            disjoint(out/'nested', [out])
        e = Evidence(self.base/'evidence', 30)
        self.addCleanup(e.close)
        e.put('x', b'123')
        with self.assertRaises(FileExistsError):
            e.put('x', b'456')
        with self.assertRaises(ValueError):
            e.put('over', b'x'*30)
        with self.assertRaises(FileExistsError):
            Evidence(e.out, 30)
        e.out.rename(self.base/'moved')
        e.out.symlink_to(self.base/'moved', target_is_directory=True)
        with self.assertRaises(ValueError):
            e.put('y', b'1')
        self.assertFalse((self.base/'moved/y').exists())

    def test_selection_contract_no_carry_15round_rejected_or_future_inputs(self):
        row = dict(seed=32001, pool='normal')
        for alive, rejected, expected in ((True, 0, True), (False, 0, False), (True, 1, False)):
            data = demo(32001, alive=alive, rejected=rejected)
            fields = S.outcome(data, row, GAME, POOL)
            self.assertIs(A.complete_contract(data, {**row, **fields}, GAME, POOL), expected)
        data = demo(32001)
        t = data['terminal']
        t['rounds'] = t['summary']['roundsPassed'] = 15
        for player in t['summary']['players']:
            player['roundsPassed'] = 15
        self.assertFalse(S.outcome(data, row, GAME, POOL)['eligiblePositive'])
        data = demo(32001)
        data['samples'][0]['seed'] = 32001
        with self.assertRaises(ValueError):
            S.outcome(data, row, GAME, POOL)

    def test_original_old_admission_and_reference_reordering(self):
        directory = self.base/'old'
        directory.mkdir()
        games = [dict(seed=s, pool=p, status='not-run') for s, p in A.PLAN]
        for row in games[:6]:
            data = demo(row['seed'], row['pool'])
            data['samples'] = [copy.deepcopy(data['samples'][0]) for _ in range(83)]
            data['terminal']['steps'] = 83
            row.update(S.outcome(data, row, GAME, POOL), status='complete', startedAt='s', finishedAt='e')
            row['artifact'] = f"{row['pool']}-{row['seed']}.json.gz"
            path = directory/row['artifact']
            path.write_bytes(gzip.compress(C.encoded(data), mtime=0))
            row.update(artifactSHA256=C.sha(path), bytes=path.stat().st_size)
        # Six distinct episode groups are required; use every second row instead.
        for i in range(3):
            games[2*i+1]['seed'] = 30004+i
        # Rebuild the plan correctly by placing those rows at their original plan positions.
        completed = [r for r in games[:6]]
        games = [dict(seed=s, pool=p, status='not-run') for s, p in A.PLAN]
        for row in completed:
            if row['pool'] == 'mixed':
                oldpath = directory/row['artifact']
                data = demo(row['seed'], row['pool'])
                data['samples'] = [copy.deepcopy(data['samples'][0]) for _ in range(83)]
                data['terminal']['steps'] = 83
                row.update(S.outcome(data, row, GAME, POOL))
                row['artifact'] = f"{row['pool']}-{row['seed']}.json.gz"
                oldpath.unlink()
                path = directory/row['artifact']
                path.write_bytes(gzip.compress(C.encoded(data), mtime=0))
                row.update(artifactSHA256=C.sha(path), bytes=path.stat().st_size)
            games[A.PLAN.index((row['seed'], row['pool']))] = row
        sources = {'/synthetic/code/'+n: h for n, h in N.PINS['files'].items() if n.startswith(('rl/', 'coop/'))}
        sources['/synthetic/'+N.PINS['collector']['name']] = N.PINS['collector']['sha256']
        m = dict(status='incomplete', finishedAt='e', planned=2048, pairedTrainingSeeds=1024,
                 sourceSHA256=sources, games=games)
        self.write(directory/'manifest.json', m)
        e = Evidence(self.base/'old-evidence', MIB)
        self.addCleanup(e.close)
        samples, md = A.admit(directory, C.sha(directory/'manifest.json'), GAME, POOL, e)
        self.assertEqual((len(samples), md['positiveGames'], md['eligibleGroupCount']), (498, 6, 6))
        wanted = list(reversed(md['labels']))
        reordered, newmd = reorder_old(samples, md, dict(labels=wanted))
        self.assertEqual(newmd['originalAdmissionIndices'], list(reversed(range(498))))
        self.assertIs(reordered[0], samples[-1])
        wanted[0] = {**wanted[0], 'labelIndex': 9000}
        with self.assertRaises(ValueError):
            reorder_old(samples, md, dict(labels=wanted))

    def test_receipt_binds_source_game_both_corpora_and_reference(self):
        source = S.source_identity()
        value = dict(kind='scale-bc-source-acceptance/v1', accepted=True, acceptedBy='synthetic-main',
                     acceptedAt='synthetic-time', evidence='synthetic-test-only', bindings=dict(
                     newManifestSHA256='b'*64, oldManifestSHA256=OLD_SHA,
                     newSourceSHA256=C.digest(C.encoded(source)), gameSHA256=C.digest(C.encoded(GAME)),
                     referenceReportSHA256=REFERENCE_REPORT_SHA, parentSHA256=PARENT_SHA))
        path = self.base/'receipt.json'
        self.write(path, value)
        receipt(path, C.sha(path), 'b'*64, source, GAME)
        for sha, src, game in [('c'*64, source, GAME), ('b'*64, {}, GAME), ('b'*64, source, {})]:
            with self.assertRaises(ValueError):
                receipt(path, C.sha(path), sha, src, game)
        value['accepted'] = False
        self.write(path, value)
        with self.assertRaises(ValueError):
            receipt(path, C.sha(path), 'b'*64, source, GAME)

    def test_original_deterministic_budget_fresh_adam_and_roundtrip(self):
        L = C.learner(CODE)
        L.torch.manual_seed(123)
        parent = L.ActorCritic(2, 2, 2)
        parent.eval().requires_grad_(False)
        initial = parameter_shas(L, parent)
        samples = [dict(features={'global': [float(i)/7, 0.], 'candidates': [[1., 0.], [-1., 0.]],
                                 'mask': [True, True]}, action=i % 2) for i in range(7)]
        previous, full_order = None, None
        for i, epochs in enumerate((15, 1, 1)):
            model, opt, record, draws = (original_train(L, parent, samples) if epochs == 15 else
                                        load_fit().train_arm(L, parent, samples, epochs))
            self.assertEqual((record['actualUpdates'], record['actualDraws']), (epochs*200, epochs*6800))
            self.assertEqual(draws.accesses, sum(load_fit().epoch_orders(L, epochs), []))
            self.assertEqual(set(record['adamSteps'].values()), {epochs*200})
            self.assertEqual(record['initialCriticHeadSHA256'], record['finalCriticHeadSHA256'])
            now = parameter_shas(L, model)
            if epochs == 15:
                full_order = draws.accesses
            else:
                self.assertEqual(draws.accesses, full_order[:6800])
            if i == 2:
                self.assertEqual(now, previous)
            previous = now
            e = Evidence(self.base/('model'+str(i)), MIB)
            try:
                identity, ckpt = save_standard(L, model, opt, dict(schema=fixture.SCHEMA, rules=C.RULES), record, e, 'tiny')
                self.assertEqual(ckpt['dimensions'], [2, 2, 2])
                e.verify()
            finally:
                e.close()
            self.assertEqual(parameter_shas(L, parent), initial)
        for bad in ([], samples*972, [{**samples[0], 'teacherPool': 'mixed'}]):
            with self.assertRaises(ValueError):
                original_train(L, parent, bad)
        # Boundary coverage uses original cyclic container and actual full epoch order.
        labels = [dict(corpus='new8192', seed=32001+i//2, pool=('normal', 'mixed')[i%2],
                       episodeGroup=32001+i//2) for i in range(6800)]
        draws = N.T.Draws([samples[0]]*6800, list(range(6800)))
        order = sum(load_fit().epoch_orders(L, 15), [])
        result = counts(labels, [draws.indices[i] for i in order])
        self.assertEqual(result['actualLabelDrawCounts'], [15]*6800)
        self.assertEqual(result['episodeGroupCount'], 3400)
        self.assertEqual([p['draws'] for p in result['pools']], [51000, 51000])

    def test_real_cli_admission_and_post_guard_failure_no_training(self):
        out, _, source = self.batch()
        old, ref = self.base/'old', self.base/'reference'
        old.mkdir()
        ref.mkdir()
        value = dict(kind='scale-bc-source-acceptance/v1', accepted=True, acceptedBy='synthetic-main',
                     acceptedAt='synthetic-time', evidence='synthetic-test-only', bindings=dict(
                     newManifestSHA256=C.sha(out/'manifest.json'), oldManifestSHA256=OLD_SHA,
                     newSourceSHA256=C.digest(C.encoded(source)), gameSHA256=C.digest(C.encoded(GAME)),
                     referenceReportSHA256=REFERENCE_REPORT_SHA, parentSHA256=PARENT_SHA))
        receipt_path = self.base/'receipt.json'
        self.write(receipt_path, value)

        def synthetic_old_reference(old_dir, new_dir, sha, ref_dir, game, collector, evidence, deadline, names):
            # Only historical fixed external assets are substituted. The new corpus uses
            # the real full-plan admission, and the final inventory checks are real.
            samples, md, _ = new_admit(new_dir, sha, game, collector, deadline)
            old_labels = [{**r, 'corpus': 'old6'} for r in md['labels']]
            metadata = dict(old=dict(labels=old_labels), new=md, oldInventory=inventory(old, deadline),
                            referenceInventory=inventory(ref, deadline))
            return dict(old6Reference=samples, newOnly=samples, combined=samples+samples), metadata

        for drift in (False, True):
            destination = self.base/('cli-'+str(drift))
            argv = ['admission-only', '--root', str(self.base), '--new-input', str(out), '--old-input', str(old),
                    '--old-reference', str(ref), '--out', str(destination),
                    '--manifest-sha256', C.sha(out/'manifest.json'), '--source-acceptance-receipt', str(receipt_path),
                    '--receipt-sha256', C.sha(receipt_path)]
            games = [(self.base/'game', GAME), (self.base/'game', {**GAME, 'drift': True} if drift else GAME)]
            with patch('run.game_identity', side_effect=games), patch('run.parent_path', return_value=self.base/'parent'), \
                 patch('run.admit_all', side_effect=synthetic_old_reference), patch('run.C.learner') as learner, \
                 patch('run.resource.setrlimit'), patch('run.os.sched_setaffinity'), redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 2 if drift else 0)
                learner.assert_not_called()
            result = C.parse(read(destination/'admission.json'))
            self.assertEqual(result['status'], 'failed' if drift else 'complete')
            self.assertFalse(list(destination.glob('*.pt')))
            self.assertFalse(result['evaluationExecuted'])

    def test_cli_defaults_finite_deadline_and_python310(self):
        self.assertEqual(CONFIG['actualDraws'], 102000)
        for path in HERE.glob('*.py'):
            ast.parse(path.read_text(), feature_version=(3, 10))
        p = parser()
        args = p.parse_args(['admission-only', '--root', '/tmp', '--new-input', 'new', '--old-input', 'old',
                             '--out', 'out', '--manifest-sha256', 'a'*64,
                             '--source-acceptance-receipt', 'receipt', '--receipt-sha256', 'b'*64])
        self.assertEqual((args.work_seconds, args.audit_seconds, args.ram_gib, args.output_mib), (900, 1800, 16, 256))
        budget = Budgets(.01, 1)
        try:
            with self.assertRaises(TimeoutError):
                with budget.phase('work'):
                    time.sleep(.02)
        finally:
            budget.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
