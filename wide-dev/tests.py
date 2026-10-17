"""No training, Torch, checkpoint deserialization, Node, game/static loader or network."""
import ast
from concurrent.futures import ProcessPoolExecutor
import copy
import inspect
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import contracts_wide as W
import admission_wide as A
import evaluation as V
import processes

C, P = W.C, W.P


def fixture(row, done, diagnostic=False):
    schema = dict(version='shop-v1', ids=[], bonds=[], bands=[])
    families = {'original-v1': 'upstream-bot', 'buy2-v1': 'first-offer-buy2', 'saver-v1': 'level-first-saver'}
    pool = dict(pool='normal' if row['pool'] == 'original' else 'mixed', split='eval', poolSeed=313,
                fixed=C.TEAMS[row['pool']], manifest='coop-heuristics-v1', manifestSHA256=W.F.G.pool_sha(W.CODE),
                seats=[dict(seat=i+1, id=ident, family=families[ident]) for i, ident in enumerate(C.TEAMS[row['pool']])])
    result = dict(ok=True, done=done, schema=schema, rules=C.RULES, steps=int(done), rounds=2 if done else 1,
        episode=dict(seed=row['seed'], mode='coop', difficulty='ABYSS', budget=128, partners=pool),
        observation=dict(public=dict(phase='RESULT' if done else 'PREP'),
                         own=dict(playerId='learner', seat=0, alive=not done, lp=0 if done else 1)),
        controller=P.VERSIONS[row['controller']], engineErrors=0, actionErrors=0, controllerErrors=0,
        features=dict(global_=[]), candidates=[] if done else [dict(type='ready')],
        summary=None, victory=done, reward=int(done))
    result['features'] = {'global': [0] * 90, 'candidates': [] if done else [[0] * 15], 'mask': [] if done else [True]}
    if done:
        rejection = dict(diagnostic=True, reason='pure-validation', result=dict(error='synthetic'))
        diagnostics = dict(partnerAudit={f'partner-{i}': dict(rejected=0, rejections=[]) for i in range(1, 4)},
            partnerActionErrors=0, controllerDiagnosticErrors=int(diagnostic), rejectedActions=[rejection] if diagnostic else [],
            controllerBlockedMoves=0, controllerBlockedMoveReasons=[], filteredMoves=[])
        result.update(diagnostics, controllerErrors=int(diagnostic))
        result['episode']['diagnostics'] = copy.deepcopy(diagnostics)
        result['summary'] = dict(seed=row['seed'], modeId='mode_multi_abyss', difficulty='ABYSS', victory=True,
            errors=0, roundsPassed=2, reason='synthetic-victory', players=[dict(playerId='learner' if i == 0 else f'partner-{i}',
            seat=i, victory=i != 0, alive=i != 0, roundsPassed=2 if i else 0, eliminatedRound=None if i else 1,
            lp=1 if i else 0, lineup=[], bonds=[], stats={}) for i in range(4)])
    return result


class Actor:
    def act(self, features, greedy=False):
        assert greedy is True and set(features) == {'global', 'candidates', 'mask'}
        return 0, 0, 0


_synthetic_initializations = 0


def synthetic_initialize():
    global _synthetic_initializations
    _synthetic_initializations += 1


def synthetic_process_job(crash=False, pipe=None):
    if crash or pipe is not None:
        # A Python peer in its own group stands in for P.Protocol's Node.
        subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(10)'], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pipe is not None:
            pipe.send(os.getpid())
            time.sleep(10)
        else:
            os._exit(7)
    return os.getpid(), _synthetic_initializations, sorted(os.sched_getaffinity(0)), 'torch' in sys.modules


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['WIDE_TEST_TMP'])
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def synthetic_job(self, row, mode='ok'):
        first, terminal = fixture(row, False), fixture(row, True, diagnostic=True)
        if mode == 'controller':
            first['controller'] = 'wrong'
        if mode == 'event':
            terminal['engineErrors'] = 1
        if mode == 'stop':
            first = dict(ok=False, error='PREP_TIMING_STOP: synthetic')
        # Original Protocol, talking only to a Python peer. Explicit P seam;
        # production run_game does not expose or supply this argument.
        script = "import sys,json\nfor line in sys.stdin:\n c=json.loads(line)\n"
        script += " if c['cmd']=='close':\n  print('{\"ok\":true}',flush=True)\n  break\n"
        script += ' print(' + repr(C.encoded(first).decode().strip()) + " if c['cmd']=='reset' else "
        script += repr(C.encoded(terminal).decode().strip()) + ',flush=True)\n'
        if mode == 'extra':
            script += "print('extra',flush=True)\n"
        if mode == 'stderr':
            script += "sys.stderr.write('synthetic failure')\n"
        if mode == 'exit':
            script += 'raise SystemExit(1)\n'
        if mode == 'timeout':
            script = 'import time; time.sleep(2)'
        def protocol(command, env, directory, deadline, byte_limit):
            self.assertEqual(command, ['node', '--no-warnings', '--loader', str(W.CODE / 'prep-timing/loader.mjs'),
                                      str(W.CODE / 'coop/env.mjs'), '--game', '/synthetic/game'])
            self.assertEqual(env['ARK_PREP_TIMING'], row['controller'])
            self.assertEqual(env['COOP_TEAM'], ','.join(C.TEAMS[row['pool']]))
            self.assertEqual((env['COOP_SPLIT'], env['COOP_POOL_SEED'], env['OMP_NUM_THREADS']), ('eval', '313', '1'))
            return P.E.Protocol([sys.executable, '-B', '-c', script], env, directory, deadline, byte_limit)
        result, files = P.game_job(row, Actor(), dict(schema=fixture(row, False)['schema']), Path('/synthetic/game'),
            self.out, time.monotonic() + (.05 if mode == 'timeout' else 1), W.MIB, W.F.G.pool_sha(W.CODE), protocol=protocol)
        seal = V.episode_seal(self.out / row['id'], time.monotonic()+1)
        self.assertEqual(files, seal['files'])
        V.verify_job(result, self.out, seal, W.MIB, time.monotonic()+1)
        return result, seal

    def test_real_p_job_controller_arbitrary_model_terminal_eof(self):
        self.assertIs(P.game_job.__defaults__[-1], P.E.Protocol)
        for i, controller in enumerate(P.VERSIONS):
            row = V.schedule()[i]
            row.update(id=f'arbitrary-{i}', model='no-name-whitelist', controller=controller)
            result, _ = self.synthetic_job(row)
            self.assertEqual(result['status'], 'complete', result['error'])
            self.assertTrue(result['eof'])
            self.assertTrue(result['teamVictory'])
            self.assertFalse(result['learnerIndividualVictory'])
            terminal = fixture(row, True, diagnostic=True)
            with self.assertRaises(ValueError):
                C.terminal_contract(terminal, seed=row['seed'], steps=1, schema=terminal['schema'], teacher=True)
        node = ast.parse((W.HERE / 'workers.py').read_text())
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'P.game_job']
        self.assertEqual(len(calls), 1)
        self.assertEqual((len(calls[0].args), calls[0].keywords), (8, []))

    def test_spawn_reuse_affinity_and_orphan_cleanup(self):
        original = os.sched_getaffinity(0)
        os.sched_setaffinity(0, sorted(original)[:2])
        processes.adopt_orphans()
        protected = processes.infrastructure_pids()
        pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'),
                                   initializer=synthetic_initialize)
        try:
            first = pool.submit(synthetic_process_job).result(timeout=2)
            second = pool.submit(synthetic_process_job).result(timeout=2)
            self.assertEqual(first, second)
            self.assertNotEqual(first[0], os.getpid())
            self.assertEqual(first[1:], (1, sorted(original)[:2], False))
            with self.assertRaises(Exception):
                pool.submit(synthetic_process_job, True).result(timeout=2)
        finally:
            killed = processes.shutdown(pool, protected=protected)
            os.sched_setaffinity(0, original)
        self.assertTrue(killed)
        self.assertFalse(set(processes.descendants()) - protected)

    def test_worker_proof_required_before_parent_seal(self):
        row, original = self.synthetic_job(V.schedule()[0])
        identity = dict(parameterSHA256='synthetic-parameter-sha')
        config = dict(models={row['model']: identity}, files={row['model']: {'sha256': 'synthetic-file-sha'}},
                      source={'synthetic': True}, affinity=[0], deadline=time.monotonic()+2)
        proof = dict(kind='wide-dev/inference-proof-v1', job=row['id'], model=row['model'], pid=123,
            before=A.proof_identity(identity), after=A.proof_identity(identity), frozen=True, intraThreads=1, interThreads=1, affinity=[0],
            checkpoint=config['files'][row['model']], sourceSHA256=C.digest(C.encoded(config['source'])))
        for key, value in (('after', {'parameterSHA256': 'drift'}), ('frozen', False), ('intraThreads', 2),
                           ('sourceSHA256', 'wrong'), ('checkpoint', {}), ('job', 'wrong')):
            with self.assertRaisesRegex(ValueError, 'inference proof'):
                V.accept_worker(row, row, {**original, 'workerProof': {**proof, key: value}}, config, self.out, W.MIB)
            self.assertFalse((self.out / row['id'] / 'inference-proof.json').exists())
        result, sealed = V.accept_worker(row, row, {**original, 'workerProof': proof}, config, self.out, W.MIB)
        V.verify_job(result, self.out, sealed, W.MIB, time.monotonic()+1)
        path = self.out / row['id'] / 'inference-proof.json'
        path.write_bytes(b'{}\n')
        with self.assertRaises(ValueError):
            V.verify_job(result, self.out, sealed, W.MIB, time.monotonic()+1)

    def test_hard_pool_deadline_cleans_worker_and_private_child_group(self):
        processes.adopt_orphans()
        protected = processes.infrastructure_pids()
        context = multiprocessing.get_context('spawn')
        reader, writer = context.Pipe(duplex=False)
        pool = ProcessPoolExecutor(max_workers=1, mp_context=context, initializer=synthetic_initialize)
        try:
            future = pool.submit(synthetic_process_job, False, writer)
            self.assertTrue(reader.poll(2), 'Synthetic worker did not start')
            reader.recv()
            processes.shutdown(pool, hard=True, protected=protected)
            with self.assertRaises(Exception):
                future.result(timeout=1)
            self.assertFalse(set(processes.descendants()) - protected)
        finally:
            processes.shutdown(pool, hard=True, protected=protected)
            reader.close()
            writer.close()

    def test_original_event_stop_close_eof_timeout_failures(self):
        for i, mode in enumerate(('controller', 'event', 'stop', 'extra', 'stderr', 'exit', 'timeout')):
            result, _ = self.synthetic_job(V.schedule()[i], mode)
            self.assertEqual(result['status'], 'stopped' if mode == 'stop' else 'failed')
            self.assertFalse(result['eof'])

    def test_fixed_paired_denominators_and_technical_fail_distinction(self):
        rows = V.schedule()
        self.assertEqual(len({r['id'] for r in rows}), 2048)
        self.assertEqual({r['seed'] for r in rows}, set(range(1, 257)))
        self.assertEqual({r['model'] for r in rows}, {'parent', 'steps3000'})
        rows[0].update(status='complete', teamVictory=False, learnerIndividualVictory=False)
        rows[1].update(status='complete', teamVictory=True, learnerIndividualVictory=True)
        rows[8].update(status='failed')
        rows[9].update(status='complete', teamVictory=True, learnerIndividualVictory=False)
        rows[2].update(status='stopped')
        s = V.summary(rows)
        self.assertEqual((s['denominator'], s['pairedSeedGroups']), (2048, 256))
        self.assertTrue(all(c['denominator'] == 256 for c in s['cells'].values()))
        pair = s['paired']['off/original']
        self.assertEqual((pair['bothComplete'], pair['incompletePairs']), (1, 255))
        self.assertEqual(pair['team']['baselineFailCandidateWin'], 1)
        self.assertEqual(pair['individual']['baselineFailCandidateWin'], 1)
        self.assertEqual(pair['team']['baselineTechnicalFailureCandidateWin'], 1)
        V.invalidate(rows, 'synthetic drift')
        self.assertEqual((V.summary(rows)['teamWins'], V.summary(rows)['individualWins']), (0, 0))

    def test_scheduler_all_2048_and_byte_reservations(self):
        models = {name: (None, None) for name in W.MODELS}
        seen = []
        def job(row, *args):
            seen.append(row['id'])
            return {**row, 'status': 'complete', 'teamVictory': True, 'learnerIndividualVictory': False}, {'bytes': 1}
        rows = V.schedule()
        seals, errors = V.dispatch(rows, models, None, self.out, time.monotonic()+2, 16, 1, W.MIB, None, job=job)
        self.assertEqual((len(seals), errors, len(set(seen))), (2048, [], 2048))
        self.assertEqual(V.summary(rows)['complete'], 2048)
        rows = V.schedule()
        seals, errors = V.dispatch(rows, models, None, self.out, time.monotonic()+2, 16, 1, W.MIB, None,
                                   job=job, total_bytes=W.MIB, metadata_bytes=1)
        self.assertFalse(seals)
        self.assertEqual(V.summary(rows)['unrun'], 2048)
        def full(row, *args):
            return {**row, 'status': 'failed'}, {'bytes': W.MIB}
        rows = V.schedule()
        seals, errors = V.dispatch(rows, models, None, self.out, time.monotonic()+2, 16, 1, W.MIB, None,
                                   job=full, total_bytes=2*W.MIB+1, metadata_bytes=1)
        self.assertEqual((len(seals), errors, V.summary(rows)['unrun']), (2, [], 2046))
        rows = V.schedule()
        V.dispatch(rows, models, None, self.out, time.monotonic()-1, 16, 1, W.MIB, None, job=job)
        self.assertEqual(V.summary(rows)['unrun'], 2048)

    def test_future_exception_stops_dispatch_drains_and_revokes(self):
        rows = V.schedule()
        seen, drained = [], []
        ready = threading.Barrier(4)
        def job(row, *args):
            seen.append(row['id'])
            ready.wait(timeout=1)
            if row['id'] == rows[0]['id']:
                raise RuntimeError('synthetic unsealed publication')
            time.sleep(.03)
            drained.append(row['id'])
            return {**row, 'status': 'complete', 'teamVictory': True, 'learnerIndividualVictory': True}, {'bytes': 0}
        seals, errors = V.dispatch(rows, {n: (None, None) for n in W.MODELS}, None, self.out,
            time.monotonic()+2, 4, 1, W.MIB, None, job=job)
        self.assertEqual((len(seen), len(drained), len(seals)), (4, 3, 3))
        self.assertTrue(errors and rows[0]['evidenceUnsealed'])
        self.assertEqual((V.summary(rows)['unrun'], V.summary(rows)['teamWins']), (2044, 0))

    def test_payload_result_and_directory_guards(self):
        row, seal = self.synthetic_job(V.schedule()[0])
        directory = self.out / row['id']
        for name in seal['files']:
            path = directory / name
            original = path.read_bytes()
            path.write_bytes(original+b' ')
            with self.assertRaises(ValueError):
                V.verify_job(row, self.out, seal, W.MIB, time.monotonic()+1)
            path.write_bytes(original)
        result = directory / 'result.json'
        data = C.parse(result.read_bytes())
        data['teamVictory'] = False
        result.write_bytes(C.encoded(data))
        forged_seal = V.episode_seal(directory, time.monotonic()+1)
        with self.assertRaisesRegex(ValueError, 'body/payload'):
            V.verify_job(row, self.out, forged_seal, W.MIB, time.monotonic()+1)
        moved = self.out / 'old'
        directory.rename(moved)
        shutil.copytree(moved, directory)
        with self.assertRaises(ValueError):
            V.verify_job(row, self.out, seal, W.MIB, time.monotonic()+1)
        shutil.rmtree(directory)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            V.episode_seal(directory, time.monotonic()+1)

    def test_general_guard_inodes_content_empty_dir_symlink(self):
        for mutation in ('content', 'inode', 'empty', 'symlink', 'ancestor'):
            d = self.out / mutation
            d.mkdir()
            p = d / 'file'
            p.write_bytes(b'a')
            guard = W.Guard()
            guard.directory(d)
            guard.verify(time.monotonic()+1)
            if mutation == 'content':
                p.write_bytes(b'b')
            elif mutation == 'inode':
                p.rename(d / 'old')
                p.write_bytes(b'a')
                (d / 'old').unlink()
            elif mutation == 'empty':
                (d / 'new').mkdir()
            elif mutation == 'symlink':
                (d / 'new').symlink_to(p)
            else:
                d.rename(self.out / 'old-parent')
                d.symlink_to(self.out / 'old-parent', target_is_directory=True)
            with self.assertRaises(ValueError):
                guard.verify(time.monotonic()+1)

    def test_fake_complete_report_cannot_admit_or_load(self):
        directory = self.out / 'bc-fit-budget-train-v1'
        directory.mkdir()
        (directory / 'steps3000.pt').write_bytes(b'synthetic, never deserialized')
        report = dict(status='complete', kind='bc-fit-budget/v1', creditGranted=True,
                      positiveLabels=498, positiveGames=6, models={'steps3000': dict(actualUpdates=3000, actualDraws=102000)})
        (directory / 'train.json').write_bytes(C.encoded(report))
        class NoLoader:
            def load_checkpoint(self, path):
                raise AssertionError('Fake report reached checkpoint loader')
        with self.assertRaisesRegex(ValueError, 'original F'):
            A.admit(NoLoader(), directory, self.out / 'parent', W.digest(directory / 'steps3000.pt')['sha256'],
                    W.digest(directory / 'train.json')['sha256'], {}, {'schema': {}}, {})
        with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
            A.admit(NoLoader(), directory, self.out / 'parent', '0'*64,
                    W.digest(directory / 'train.json')['sha256'], {}, {'schema': {}}, {})

    def test_actual_cli_empty_root_preserves_manifest_before_subprocess(self):
        trained = self.out / 'bc-fit-budget-train-v1'
        trained.mkdir()
        collector = self.out / 'collector.py'
        collector.write_bytes(b'# synthetic')
        (trained / 'input-metadata.json').write_bytes(C.encoded(dict(rawFileSets={}, inputFiles={str(collector): 'synthetic'})))
        command = [sys.executable, '-B', str(W.HERE / 'run.py'), 'eval', '--dev-only', '--root', str(self.out),
                   '--experiment-code', str(W.CODE), '--trained', 'bc-fit-budget-train-v1', '--out', 'eval',
                   '--candidate-sha256', '0'*64, '--train-json-sha256', '1'*64]
        result = subprocess.run(command, env={**os.environ, 'PATH': '/nonexistent'}, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        report = C.parse((self.out / 'eval/summary.json').read_bytes())
        manifest = C.parse((self.out / 'eval/manifest.json').read_bytes())
        self.assertEqual(len(manifest['games']), 2048)
        self.assertEqual((report['denominator'], report['unrun'], report['teamWins']), (2048, 2048, 0))
        self.assertFalse(report['creditGranted'])
        self.assertIn('Input file/size', report['error'])
        self.assertFalse(manifest['unseenSeedsClaim'])

    def test_pins_and_syntax_no_training_calls(self):
        source = W.source_identity(W.CODE)
        self.assertEqual(source['base'], W.BASE)
        self.assertEqual(source['fit'], W.F.source_identity(W.CODE))
        for path in W.HERE.glob('*.py'):
            tree = ast.parse(path.read_text(), feature_version=(3, 10))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    self.assertNotIn(ast.unparse(node.func), ('L.bc_update', 'FT.fit', 'FT.train_arm'))
        self.assertNotIn('torch', sys.modules)
        self.assertEqual(Path(inspect.getsourcefile(P.game_job)), W.CODE / 'prep-timing/run.py')


if __name__ == '__main__':
    unittest.main(verbosity=2)
