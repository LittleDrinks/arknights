"""Queue admission, filesystem discovery and saved-result progress integration."""
import json
from pathlib import Path
import tempfile
import threading
import time
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from arknights.evaluate.dashboard import Evaluations, latest_state, record_process
import test_evaluate
from arknights.evaluate import evaluate


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_evaluate.EvaluationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.registry = Evaluations(self.root)
        self.addCleanup(self.registry.close)

    def test_direct_evaluation_appears_and_actual_outcomes_remain_separate(self):
        run = evaluate(self.fixture.script, root=self.root, seeds=[1, 3], timeout=3)
        listed = self.registry.list()['jobs']
        self.assertEqual(len(listed), 1)
        row = listed[0]
        self.assertEqual(row['runId'], run.path.name)
        self.assertEqual(row['progress']['complete'], 2)
        self.assertEqual(row['progress']['teamWins'], 2)
        self.assertEqual(row['progress']['learnerWins'], 1)
        detail = self.registry.detail(row['id'])
        self.assertEqual(len(detail['games']), 2)
        self.assertEqual(detail['games'][0]['replay']['status'], 'absent')

    def test_queue_is_persistent_validates_admission_and_keeps_source_identity(self):
        job = self.registry.submit({'script': 'policy.py', 'seeds': '1:2'})
        self.assertEqual(job['planned'], 2)
        self.assertEqual(job['status'], 'queued')
        self.assertIn(str(self.fixture.script), job['policySources'])
        restarted = Evaluations(self.root)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.list()['queue']['waiting'], 1)
        for args in ({'script': '../escape.py'}, {'script': 'policy.py', 'seeds': '100001'},
                     {'script': 'policy.py', 'workers': 0}, {'script': 'policy.py', 'timeout': float('nan')},
                     {'script': 'policy.py', 'partners': ['mixed', 'mixed']}):
            with self.subTest(args=args), self.assertRaises((ValueError, TypeError)):
                self.registry.submit(args)
        linked = self.root / 'linked.py'
        linked.symlink_to(self.fixture.script)
        with self.assertRaises(ValueError):
            self.registry.submit({'script': 'linked.py'})

    def test_restart_marks_active_job_failed_without_replaying_it(self):
        j = self.registry.submit({'script': 'policy.py', 'seeds': '1'})
        p = self.registry.directory / 'jobs' / (j['id'] + '.json')
        j['status'] = 'running'
        p.write_text(json.dumps(j))
        self.registry.close()
        restarted = Evaluations(self.root)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.detail(j['id'])['status'], 'failed')
        self.assertIn('restarted', restarted.detail(j['id'])['error'])

    def test_crash_recovery_reaps_recorded_group_before_new_work(self):
        job = self.registry.submit({'script': 'policy.py', 'seeds': '1'})
        p = self.registry.directory / 'jobs' / (job['id'] + '.json')
        job['status'] = 'running'
        p.write_text(json.dumps(job))
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        record_process(self.registry.directory / (job['id'] + '.processes'), child.pid)
        self.registry.close()
        restarted = Evaluations(self.root)
        self.addCleanup(restarted.close)
        child.wait(timeout=3)
        self.assertNotEqual(child.returncode, 0)
        self.assertEqual(restarted.detail(job['id'])['status'], 'failed')

    def test_recursive_policy_sources_and_inventory_are_captured(self):
        folder = self.root / 'helpers'
        folder.mkdir()
        helper = folder / 'policy.py'
        helper.write_text('def choose(): return 1\n')
        job = self.registry.submit({'script': 'policy.py', 'seeds': '1'})
        self.assertIn(str(helper), job['policySources'])

    def test_live_prefix_tolerates_partial_json_line(self):
        folder = self.root / 'live'
        folder.mkdir()
        (folder / 'protocol.jsonl').write_text('{"state":{"round":4,"own":{"lp":8}}}\n{"state":')
        self.assertEqual(latest_state(folder)['round'], 4)

    def test_completed_cases_prepare_replays_without_a_browser_request(self):
        evaluate(self.fixture.script, root=self.root, seeds=[1, 3], timeout=3)
        seen = []
        def prepare(job, episode, path):
            seen.append(episode)
            path.write_text(json.dumps({'status': 'ready', 'url': '/?replay=fixture'}))
        with patch.object(self.registry, '_replay', side_effect=prepare):
            self.registry.prepare_replays()
            deadline = time.monotonic() + 2
            while not seen and time.monotonic() < deadline:
                time.sleep(.01)
            self.registry.prepare_replays()
            self.registry.replays.shutdown(wait=True)
        self.assertEqual(seen, ['0001-original-s1', '0002-original-s3'])
        self.registry.prepare_replays()
        self.assertEqual(len(seen), 2)

    def test_fifo_dispatches_once_and_obeys_service_cancellation(self):
        first = self.registry.submit({'script': 'policy.py', 'seeds': '1'})
        second = self.registry.submit({'script': 'policy.py', 'seeds': '2'})
        seen = []
        def execute(command, **kwargs):
            from types import SimpleNamespace
            key = command[-1]
            seen.append(key)
            p = self.registry.directory / 'jobs' / (key + '.json')
            value = json.loads(p.read_text())
            value['status'] = 'complete'
            p.write_text(json.dumps(value))
            if len(seen) == 2:
                self.registry.stop.set()
            return SimpleNamespace(returncode=0, reason=None)
        with patch('arknights.evaluate.dashboard.run_command', side_effect=execute):
            thread = threading.Thread(target=self.registry.dispatch)
            thread.start()
            thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen, [first['id'], second['id']])


if __name__ == '__main__':
    unittest.main()
