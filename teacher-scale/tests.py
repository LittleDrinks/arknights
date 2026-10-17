"""Fault-injection tests using tiny synthetic data and Python line servers only."""
import ast
import copy
import gzip
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from reuse import A, C, CODE, E, HERE, outcome, plan, source_identity
from storage import Budget, Sink, case_raw, identity, new_output, publish
from wire import LaneProtocol, Registry, WireBudget
from collector import case_name, lane_task, run_lanes
from reconcile import reconcile
from fixture import GAME, POOL, demo
from run import emergency_rows, affinity


def small_plan(n=2):
    return [dict(index=i, seed=32001+i, pool='normal', lane='normal-00', status='not-run') for i in range(n)]


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TEACHER_TEST_TMP'])
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, n=2):
        out = new_output(self.base / ('out-'+str(len(list(self.base.iterdir())))))
        for name in ('cases', 'journals', 'lanes'):
            (out / name).mkdir()
        rows, budget = small_plan(n), Budget(1_000_000, 100_000)
        publish(out / 'plan.json', dict(games=rows, planned=n, split='train'), budget)
        for name in ('source-before.json', 'source-after.json'):
            publish(out / name, dict(synthetic=True), budget)
        manifest = dict(games=rows, lanes={}, globalErrors=[], status='running')
        publish(out / 'manifest.json', manifest, budget)
        return out, rows, budget, manifest

    def run_batch(self, mode='good', n=2, limit=65536):
        out, rows, budget, manifest = self.prepare(n)
        env = {**os.environ, 'FAKE_PID': str(self.base / 'pid')}
        command = [sys.executable, '-B', str(HERE / 'fake_child.py'), mode]
        now = time.monotonic()
        run_lanes(out, rows, {'normal': (command, env)}, GAME, POOL, budget, manifest,
                  now+1.2, now+1.8, threading.Event(), request_seconds=.3, byte_limit=limit)
        audit = reconcile(out, small_plan(n), time.monotonic()+2, byte_limit=limit)
        return out, rows, budget, manifest, audit

    def test_fixed_production_plan_and_readonly_reuse(self):
        rows = plan()
        self.assertEqual(len(rows), 8192)
        self.assertEqual([(r['seed'], r['pool']) for r in rows],
                         [(s, p) for s in range(32001, 36097) for p in ('normal', 'mixed')])
        self.assertEqual(len({r['lane'] for r in rows}), 32)
        self.assertTrue(all(sum(r['lane'] == lane for r in rows) == 256 for lane in {r['lane'] for r in rows}))
        self.assertTrue(issubclass(LaneProtocol, E.Protocol))
        self.assertEqual(source_identity()['base'], 'ffbe8cc6b994f0676cb386b7c9f88d9e8281333f')
        self.assertNotIn('torch', sys.modules)
        self.assertNotIn('rl.learner', sys.modules)
        for p in HERE.glob('*.py'):
            ast.parse(p.read_text(), feature_version=(3, 10))

    def test_persistent_raw_journals_and_independent_freeze(self):
        out, rows, budget, manifest, audit = self.run_batch()
        self.assertEqual(audit['errors'], [])
        self.assertEqual([r['status'] for r in audit['games']], ['complete']*2)
        self.assertEqual(len(audit['lanes']), 1)
        self.assertTrue(audit['lanes']['normal-00']['closeACKAndProcessEOF'])
        self.assertTrue(all(r['processEOF'] is False for r in rows))
        wire = gzip.decompress((out / 'lanes/normal-00/stdout.gz').read_bytes())
        self.assertEqual(len(wire.splitlines()), 3)
        for r in audit['games']:
            raw = gzip.decompress((out / r['artifact']).read_bytes())
            self.assertEqual(raw, wire[r['stdoutStart']:r['stdoutEnd']])
        self.assertFalse(list(out.rglob('protocol.jsonl')))
        self.assertEqual(budget.used, sum(p.stat().st_size for p in out.rglob('*') if p.is_file()))
        self.assertEqual(manifest['wireBytes'], len(wire))

    def test_each_result_arrives_before_lane_finishes(self):
        out, rows, budget, _ = self.prepare(6)
        events, stop = queue.Queue(), threading.Event()
        now = time.monotonic()
        t = threading.Thread(target=lane_task, args=(rows, out,
            [sys.executable, '-B', str(HERE / 'fake_child.py'), 'slow'], os.environ.copy(), GAME, POOL,
            budget, WireBudget(6*65536), stop, Registry(), events, now+2, now+3, 1, 65536))
        t.start()
        try:
            while True:
                kind, r = events.get(timeout=1)
                if kind == 'case' and r['status'] == 'complete':
                    self.assertTrue(t.is_alive())
                    self.assertTrue((out / 'journals' / (case_name(r)+'.completed.json')).exists())
                    self.assertTrue((out / r['artifact']).exists())
                    break
        finally:
            t.join(timeout=3)
        self.assertFalse(t.is_alive())

    def test_teacher_contract_outcomes_and_diagnostics(self):
        row = small_plan(1)[0]
        self.assertTrue(outcome(demo(row['seed']), row, GAME, POOL)['eligiblePositive'])
        for data in (demo(row['seed'], alive=False), demo(row['seed'], rejected=1)):
            before = copy.deepcopy(data)
            fields = outcome(data, row, GAME, POOL)
            self.assertTrue(fields['teamVictory'])
            self.assertFalse(fields['eligiblePositive'])
            self.assertEqual(before, data)
        d = demo(row['seed'], rejected=1)
        reject = dict(diagnostic=True, reason='pure-validation', result=dict(error='HAND_FULL'))
        d['terminal']['rejectedActions'].append(reject)
        before = copy.deepcopy(d)
        self.assertFalse(outcome(d, row, GAME, POOL)['eligiblePositive'])
        self.assertEqual(d, before)  # teacher=True accepts original zero counters; never rewrites diagnostics
        d['terminal']['controllerErrors'] = d['terminal']['controllerDiagnosticErrors'] = 1
        with self.assertRaises(ValueError):
            outcome(d, row, GAME, POOL)
        for mutate in (lambda d: d.update(teacher='other'),
                       lambda d: d['terminal']['episode'].update(budget=127),
                       lambda d: d['samples'][0].update(action=1),
                       lambda d: d['terminal']['summary']['players'][0].update(alive=False)):
            d = demo(row['seed'])
            mutate(d)
            with self.assertRaises((ValueError, KeyError)):
                outcome(d, row, GAME, POOL)

    def test_negative_and_rejected_samples_retained(self):
        for mode in ('carry', 'rejection'):
            out, _, _, _, audit = self.run_batch(mode, n=1)
            self.assertEqual(audit['errors'], [])
            row = audit['games'][0]
            self.assertFalse(row['eligiblePositive'])
            data = C.parse(gzip.decompress((out / row['artifact']).read_bytes()))
            self.assertEqual(len(data['samples']), 2)
            self.assertIn('summary', data['terminal'])

    def test_error_and_malformed_full_response_retained(self):
        for mode in ('error', 'malformed'):
            out, _, _, _, audit = self.run_batch(mode)
            row = audit['games'][0]
            self.assertEqual(row['status'], 'failed')
            raw = gzip.decompress((out / row['artifact']).read_bytes())
            self.assertTrue(raw.endswith(b'\n'))
            if mode == 'error':
                self.assertGreater(len(C.parse(raw)['error']), 7000)
            else:
                self.assertEqual(raw, b'{bad-json}\n')
            self.assertTrue(audit['errors'])

    def test_partial_and_byte_limit_before_parse(self):
        for mode in ('partial', 'flood'):
            out, _, _, manifest, audit = self.run_batch(mode, n=1, limit=4096)
            self.assertNotEqual(audit['games'][0]['status'], 'not-run')
            self.assertTrue(audit['errors'])
            raw = gzip.decompress((out / 'lanes/normal-00/stdout.gz').read_bytes())
            self.assertLessEqual(len(raw), 4096)
            self.assertLessEqual(manifest['wireBytes'], 4096)
            self.assertEqual(raw, b'{"ok":' if mode == 'partial' else b'x'*4096)
            self.assertFalse(list((out / 'cases').iterdir()))

    def test_ack_stderr_and_trailing_wire_fail_batch(self):
        for mode in ('bad-close', 'trailing', 'stderr'):
            out, _, _, _, audit = self.run_batch(mode, n=1)
            self.assertTrue(audit['errors'])
            self.assertTrue(list((out / 'cases').glob('*.gz')))
            if mode == 'stderr':
                self.assertEqual((out / 'lanes/normal-00/stderr.log').read_bytes(), b'synthetic stderr\n')

    def test_timeout_kills_descendant_group_and_records_started(self):
        out, _, _, _, audit = self.run_batch('hang', n=1)
        self.assertTrue(audit['errors'])
        self.assertNotEqual(audit['games'][0]['status'], 'not-run')
        pid = int((self.base / 'pid').read_text())
        status = Path(f'/proc/{pid}/stat')
        if status.exists():
            self.assertEqual(status.read_text().rsplit(')', 1)[1].split()[0], 'Z')

    def test_journal_loss_after_raw_does_not_lose_execution(self):
        import collector
        original = collector.publish
        def fault(path, *args, **kwargs):
            if path.parent.name == 'journals' and path.name.endswith('.completed.json'):
                raise OSError('injected durable journal failure')
            return original(path, *args, **kwargs)
        with patch.object(collector, 'publish', side_effect=fault):
            out, _, _, _, audit = self.run_batch(n=1)
        self.assertEqual(audit['games'][0]['status'], 'interrupted')
        self.assertTrue(audit['errors'])
        self.assertEqual(len(list((out / 'cases').glob('*.gz'))), 1)

    def test_missing_orphan_duplicate_and_hash_drift(self):
        source, _, _, _, _ = self.run_batch()
        mutations = ('missing-start', 'missing-done', 'missing-raw', 'orphan', 'duplicate', 'hash', 'erased-journals')
        for mutation in mutations:
            out = self.base / mutation
            shutil.copytree(source, out)
            stem = case_name(small_plan(1)[0])
            start, done = [out / 'journals' / (stem+suffix) for suffix in ('.started.json', '.completed.json')]
            raw = out / 'cases' / (stem+'.json.gz')
            if mutation == 'missing-start': start.unlink()
            elif mutation == 'missing-done': done.unlink()
            elif mutation == 'missing-raw': raw.unlink()
            elif mutation == 'orphan': shutil.copyfile(raw, out / 'cases/unknown.json.gz')
            elif mutation == 'duplicate': shutil.copyfile(start, out / 'journals/duplicate.started.json')
            elif mutation == 'hash': raw.write_bytes(raw.read_bytes()[:-3])
            else:
                for p in (out / 'journals').iterdir(): p.unlink()
                for p in (out / 'cases').iterdir(): p.unlink()
                (out / 'lanes/normal-00/completed.json').unlink()
            audit = reconcile(out, small_plan(), time.monotonic()+1, byte_limit=65536)
            self.assertTrue(audit['errors'], mutation)
            self.assertTrue(all(r['status'] != 'not-run' for r in audit['games']), mutation)

    def test_atomic_budget_and_symlink_rejection(self):
        budget = Budget(1000, 100)
        successes = []
        def charge():
            try:
                budget.charge(80)
                successes.append(1)
            except ValueError:
                pass
        threads = [threading.Thread(target=charge) for _ in range(32)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual((budget.used, len(successes)), (880, 11))
        budget.charge(120, metadata=True)
        with self.assertRaises(ValueError): budget.charge(1, metadata=True)
        target = self.base / 'exists'
        target.mkdir()
        with self.assertRaises(FileExistsError): new_output(target)
        link = self.base / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError): new_output(link / 'nested')
        dangling = self.base / 'dangling'
        dangling.symlink_to(self.base / 'absent')
        with self.assertRaises(ValueError): new_output(dangling)

    def test_disk_failure_and_cleanup_exception_are_global_failures(self):
        import collector
        original = collector.case_raw
        def full_disk(path, raw, budget):
            budget.limit = budget.used+budget.reserve+20
            return original(path, raw, budget)
        with patch.object(collector, 'case_raw', side_effect=full_disk):
            out, _, budget, manifest, audit = self.run_batch(n=1)
        self.assertTrue(audit['errors'])
        self.assertTrue(manifest['globalErrors'])
        self.assertNotEqual(audit['games'][0]['status'], 'not-run')
        self.assertLessEqual(budget.used, budget.limit)
        close = LaneProtocol.close
        def cleanup_fault(channel):
            close(channel)
            raise OSError('injected cleanup error after group reaped')
        with patch.object(LaneProtocol, 'close', cleanup_fault):
            _, _, _, manifest, audit = self.run_batch(n=1)
        self.assertTrue(manifest['globalErrors'])
        self.assertTrue(audit['errors'])

    def test_cpu_affinity_only_narrows_inherited_mask(self):
        script = ('import os; from run import affinity; '
                  'before=sorted(os.sched_getaffinity(0))[:2]; os.sched_setaffinity(0,before); '
                  'after=affinity(); assert set(after)<=set(before) and len(after)<=32')
        result = subprocess.run([sys.executable, '-B', '-c', script], cwd=HERE,
                                capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_8192_rows_freeze_without_any_engine_or_sampling(self):
        out, _, budget, _ = self.prepare(1)
        rows = plan()
        budget.limit, budget.reserve = 4_000_000, 100_000
        publish(out / 'plan.json', dict(games=rows, planned=8192, split='train'), budget, snapshot=True)
        for lane in {r['lane'] for r in rows}:
            d = out / 'lanes' / lane
            d.mkdir()
            s = dict(lane=lane, status='started', startedAt='synthetic-start', finishedAt=None)
            si = publish(d / 'started.json', s, budget)
            publish(d / 'completed.json', {**s, 'finishedAt': 'synthetic-end', 'status': 'complete',
                    'startedIdentity': si, 'files': {}, 'spawned': False, 'wireBytes': 0, 'errors': []}, budget)
        audit = reconcile(out, rows, time.monotonic()+2)
        self.assertEqual(audit['errors'], [])
        self.assertEqual(len(audit['games']), 8192)
        self.assertTrue(all(r['status'] == 'not-run' for r in audit['games']))
        (out / 'source-after.json').write_text('{"drift":true}\n')
        audit = reconcile(out, rows, time.monotonic()+2)
        self.assertTrue(any('Source drift' in e for e in audit['errors']))

    def test_deadline_leaves_confirmed_not_run_and_complete_denominator(self):
        out, rows, budget, manifest = self.prepare(2)
        run_lanes(out, rows, {'normal': (['must-not-spawn'], {})}, GAME, POOL, budget, manifest,
                  time.monotonic()-1, time.monotonic()+1, threading.Event(), byte_limit=65536)
        audit = reconcile(out, small_plan(), time.monotonic()+1, byte_limit=65536)
        self.assertEqual(audit['errors'], [])
        self.assertEqual([r['status'] for r in audit['games']], ['not-run']*2)

    def test_failed_freeze_fallback_does_not_claim_unrun(self):
        out, rows, _, _ = self.prepare()
        (out / 'journals' / (case_name(rows[0])+'.started.json.stage')).write_text('{')
        result = emergency_rows(out, rows)
        self.assertEqual([r['status'] for r in result], ['interrupted', 'unknown'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
