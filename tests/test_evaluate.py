"""Bounded unittest integration checks; no training or production game writes."""
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from arknights.evaluate import evaluate, verify_run
from arknights.evaluate import runner
from arknights.evaluate.process import JsonlProcess, run_command


FIXTURE_BACKEND = '''import json, os, sys
state = None
for line in sys.stdin:
    request = json.loads(line)
    if request['cmd'] == 'reset':
        state = {'terminal': False, 'seed': request['seed'], 'pid': os.getpid(), 'outcome': None}
    elif request['cmd'] == 'act':
        victory = state['seed'] % 2 == 1
        state.update(terminal=True, outcome={'team': {'victory': victory}, 'summary': {'victory': victory}})
    elif request['cmd'] != 'observe':
        raise ValueError('fixture unsupported command')
    print(json.dumps({'ok': True, 'state': state}), flush=True)
'''

FIXTURE_SESSION = '''import json, sys
from pathlib import Path
from arknights.evaluate.process import JsonlProcess
class Session:
    def __init__(self, *, root, directory, timeout, max_requests, **config):
        self.root, self.directory = Path(root), Path(directory)
        self.timeout, self.maximum, self.config = timeout, max_requests, config
        self.count, self.state = 0, None
    def __enter__(self):
        self.directory.mkdir(exist_ok=False)
        self.requests = (self.directory / 'requests.jsonl').open('xb')
        self.child = JsonlProcess([sys.executable, '-B', '-u', str(Path(__file__).with_name('backend.py'))],
            timeout=self.timeout, raw_path=self.directory / 'protocol.jsonl', stderr_path=self.directory / 'stderr.log')
        self._request({'cmd': 'reset', **self.config})
        return self
    def _request(self, request):
        if self.count >= self.maximum:
            raise RuntimeError('Request budget exhausted')
        self.count += 1
        self.requests.write((json.dumps({'seq': self.count, 'request': request}) + '\\n').encode())
        self.requests.flush()
        response = self.child.request(request)
        self.state = response['state']
        if self.state['terminal'] and not (self.directory / 'terminal.json').exists():
            (self.directory / 'terminal.json').write_text(json.dumps(self.state))
        return response
    def observe(self):
        return self._request({'cmd': 'observe'})['state']
    def act(self, action):
        return self._request({'cmd': 'act', 'action': action})
    def close(self):
        try:
            self.child.close()
        finally:
            self.requests.close()
    def __exit__(self, *_):
        self.close()
'''

STRATEGY = '''def play(game):
    import os
    assert game.observe()['pid'] != os.getpid()
    game.act({'type': 'ready'})
'''


class ProcessTests(unittest.TestCase):
    def command(self, code):
        return [sys.executable, '-B', '-u', '-c', code]

    def test_supervisor_drains_stderr_and_preserves_nonzero(self):
        result = run_command(self.command("import os; os.write(2, b'e'*200000); print('prefix'); raise SystemExit(7)"), timeout=3)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.reason, 'nonzero_exit')
        self.assertEqual(result.stdout, b'prefix\n')
        self.assertEqual(len(result.stderr), 200000)

    def test_supervisor_timeout_and_explicit_cancellation(self):
        result = run_command(self.command('import time; time.sleep(20)'), timeout=.12)
        self.assertEqual(result.reason, 'timeout')
        cancel = threading.Event()
        cancel.set()
        result = run_command(self.command('import time; time.sleep(20)'), timeout=3, cancel=cancel)
        self.assertEqual(result.status, 'cancelled')

    def test_closed_pipes_do_not_bypass_deadline(self):
        code = 'import os,time; os.close(1); os.close(2); time.sleep(20)'
        self.assertEqual(run_command(self.command(code), timeout=.12).reason, 'timeout')

    def test_output_cap_is_a_failure_and_caps_saved_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, err = Path(directory) / 'out', Path(directory) / 'err'
            result = run_command(self.command("import os; os.write(1,b'x'*200000)"),
                                 stdout_path=raw, stderr_path=err, max_bytes=1024, timeout=3)
            self.assertEqual(result.reason, 'output_limit')
            self.assertLessEqual(raw.stat().st_size + err.stat().st_size, 1024)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'process group check uses /proc')
    def test_parent_exit_kills_descendant_holding_pipes(self):
        code = ("import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',"
                "'import time; time.sleep(20)']); print(child.pid,flush=True)")
        result = run_command(self.command(code), timeout=3)
        self.assertEqual(result.status, 'complete')
        pid = int(result.stdout)
        state = Path(f'/proc/{pid}/stat')
        if state.exists():
            self.assertEqual(state.read_text().rsplit(')', 1)[1].split()[0], 'Z')

    def test_jsonl_drains_stderr_and_records_raw(self):
        code = ("import sys,json,os\nfor line in sys.stdin:\n"
                " os.write(2,b'e'*200000)\n print(json.dumps({'value':json.loads(line)}),flush=True)")
        with tempfile.TemporaryDirectory() as directory:
            raw, err = Path(directory) / 'raw', Path(directory) / 'err'
            with JsonlProcess(self.command(code), raw_path=raw, stderr_path=err, timeout=3) as child:
                self.assertEqual(child.request({'n': 4}), {'value': {'n': 4}})
                self.assertEqual(child.request({'n': 5}), {'value': {'n': 5}})
            self.assertEqual(len(raw.read_text().splitlines()), 2)
            self.assertEqual(err.stat().st_size, 400000)
            self.assertTrue(child.eof)

    def test_jsonl_timeout_covers_blocked_stdin(self):
        child = JsonlProcess(self.command('import time; time.sleep(20)'), timeout=.12)
        with self.assertRaises(TimeoutError):
            child.request({'large': 'x' * 2000000})
        self.assertTrue(child.closed)
        self.assertIsNotNone(child.returncode)

    def test_jsonl_nonzero_exit_after_valid_response_is_a_failure(self):
        code = "import sys; input(); print('{}',flush=True); sys.stdin.read(); raise SystemExit(7)"
        child = JsonlProcess(self.command(code), timeout=2)
        self.assertEqual(child.request({}), {})
        with self.assertRaisesRegex(RuntimeError, 'exit cleanly: 7'):
            child.close()
        self.assertTrue(child.closed)

    def test_jsonl_malformed_eof_and_output_cap(self):
        for code, error, cap in (
            ("input(); print('invalid',flush=True)", ValueError, 1000),
            ('input()', EOFError, 1000),
            ("import os; input(); os.write(1,b'x'*10000)", RuntimeError, 100)):
            with self.subTest(code=code):
                child = JsonlProcess(self.command(code), timeout=2, max_bytes=cap)
                with self.assertRaises(error):
                    child.request({})
                self.assertTrue(child.closed)
                self.assertIsNotNone(child.returncode)

    def test_existing_output_is_never_truncated_on_spawn_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'raw'
            path.write_bytes(b'original')
            with self.assertRaises(FileExistsError):
                JsonlProcess(['/missing-program'], raw_path=path)
            self.assertEqual(path.read_bytes(), b'original')
            with self.assertRaises(FileNotFoundError):
                JsonlProcess(['/missing-program'])


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        package = self.root / 'arknights'
        (package / 'evaluate').mkdir(parents=True)
        (package / '__init__.py').write_text('')
        source = Path(runner.__file__).parent
        for name in ('__init__.py', 'runner.py', 'worker.py', 'process.py'):
            (package / 'evaluate' / name).write_bytes((source / name).read_bytes())
        (package / 'adapter').mkdir()
        (package / 'adapter/__init__.py').write_text(FIXTURE_SESSION)
        (package / 'adapter/backend.py').write_text(FIXTURE_BACKEND)
        game = self.root / 'game'
        game.mkdir()
        subprocess.run(['git', 'init', '-q', str(game)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(game), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                        'commit', '--allow-empty', '-qm', 'fixture rules'], check=True, capture_output=True)
        commit = subprocess.check_output(['git', '-C', str(game), 'rev-parse', 'HEAD'], text=True).strip()
        # Only this fixture's initial admission pin changes; children still verify
        # the captured, pristine fixture commit independently at both boundaries.
        pin = patch.object(runner, 'RULES_COMMIT', commit)
        pin.start()
        self.addCleanup(pin.stop)
        self.script = self.root / 'policy.py'
        self.script.write_text(STRATEGY)

    def run_eval(self, **kwargs):
        return evaluate(self.script, root=self.root, seeds=(1,), timeout=3, **kwargs)

    def test_isolated_terminal_outcomes_all_partner_seed_cases_and_seals(self):
        run = evaluate(self.script, root=self.root, seeds=(1, 2), partners=('original', 'mixed'),
                       workers=2, timeout=4)
        summary = verify_run(run.path)
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual((summary['planned'], summary['complete'], summary['teamWins'], summary['gameLosses']), (4, 4, 2, 2))
        self.assertEqual(summary['winRate'], .5)
        self.assertEqual(summary, run.summary)
        self.assertEqual([g['id'] for g in summary['games']],
                         ['0001-original-s1', '0002-original-s2', '0003-mixed-s1', '0004-mixed-s2'])
        self.assertNotIn(str(self.root), (run.path / 'identity.json').read_text().replace(str(self.script), ''))

    def test_policy_return_is_not_automatic_ready_or_terminal(self):
        self.script.write_text('def play(game):\n    return None\n')
        run = self.run_eval()
        summary = verify_run(run.path)
        self.assertEqual((summary['status'], summary['complete'], summary['technicalFailures'], summary['gameLosses']), ('failed', 0, 1, 0))
        row = summary['games'][0]
        directory = run.path / row['id']
        result = json.loads((directory / 'result.json').read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['prefixState']['terminal'])
        self.assertTrue((directory / 'trajectory/protocol.jsonl').stat().st_size)

    def test_timeout_preserves_all_planned_denominator(self):
        self.script.write_text('def play(game):\n    import time\n    time.sleep(20)\n')
        run = evaluate(self.script, root=self.root, seeds=(1, 2), workers=1, timeout=.3)
        summary = verify_run(run.path)
        self.assertEqual(summary['planned'], 2)
        self.assertEqual(summary['failed'], 2)
        self.assertEqual(summary['complete'], 0)
        self.assertEqual([row['process']['reason'] for row in summary['games']], ['timeout', 'timeout'])
        for row in summary['games']:
            protocol = run.path / row['id'] / 'trajectory/protocol.jsonl'
            if protocol.exists() and protocol.stat().st_size:
                pid = json.loads(protocol.read_text().splitlines()[0])['state']['pid']
                state = Path(f'/proc/{pid}/stat')
                if state.exists():
                    self.assertEqual(state.read_text().rsplit(')', 1)[1].split()[0], 'Z')

    def test_interruption_cancels_running_and_pending_cases_without_erasing_plan(self):
        self.script.write_text('def play(game):\n    import time\n    time.sleep(20)\n')
        with patch.object(runner, 'wait', side_effect=KeyboardInterrupt):
            run = evaluate(self.script, root=self.root, seeds=(1, 2, 3), timeout=3)
        summary = verify_run(run.path)
        self.assertEqual(summary['status'], 'cancelled')
        self.assertEqual((summary['planned'], summary['cancelled'], summary['complete']), (3, 3, 0))

    def test_all_failures_and_attempts_are_retained(self):
        self.script.write_text("def play(game):\n    print('policy prefix',flush=True)\n    raise RuntimeError('fixture failure')\n")
        first, second = self.run_eval(), self.run_eval()
        self.assertNotEqual(first.path, second.path)
        for run in (first, second):
            summary = verify_run(run.path)
            row = summary['games'][0]
            self.assertEqual(row['status'], 'failed')
            directory = run.path / row['id']
            self.assertIn('policy prefix', (directory / 'stdout.log').read_text())
            self.assertIn('fixture failure', (directory / 'stderr.log').read_text())
            self.assertIn('fixture failure', (directory / 'result.json').read_text())

    def test_budget_exhaustion_is_technical_failure(self):
        run = self.run_eval(max_requests=1)
        self.assertEqual(verify_run(run.path)['technicalFailures'], 1)
        self.assertEqual(run.summary['teamWins'], 0)

    def test_strategy_uses_hashed_source_even_with_timestamp_valid_stale_bytecode(self):
        bad = "def play(game):\n    raise RuntimeError('stale bytecode')\n"
        self.script.write_text(bad.ljust(len(STRATEGY)))
        py_compile.compile(str(self.script), doraise=True)
        stamp = self.script.stat()
        self.script.write_text(STRATEGY)
        os.utime(self.script, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(self.run_eval().summary['status'], 'complete')

    def test_protected_seeds_and_duplicate_plan_fail_before_outputs(self):
        for values in ((100001,), (100064,), (1, 1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                evaluate(self.script, root=self.root, seeds=values)
        with self.assertRaises(ValueError):
            self.run_eval(partners=('original', 'original'))
        self.assertFalse((self.root / 'results').exists())
        # Already exposed validation seeds remain available for explicit development.
        self.assertEqual(self.run_explicit_seed(90001).summary['complete'], 1)

    def run_explicit_seed(self, seed):
        return evaluate(self.script, root=self.root, seeds=(seed,), timeout=3)

    def test_source_change_invalidates_run_end_guards(self):
        self.script.write_text(STRATEGY + "    from pathlib import Path\n    Path(__file__).write_text('# changed')\n")
        run = self.run_eval()
        self.assertEqual(run.summary['status'], 'failed')
        self.assertIsNotNone(run.summary['guardError'])
        self.assertEqual(run.summary['complete'], 0)
        with self.assertRaisesRegex(ValueError, 'end guards'):
            verify_run(run.path)

    def test_corrupt_result_and_added_artifact_rejected(self):
        run = self.run_eval()
        directory = run.path / run.summary['games'][0]['id']
        original = (directory / 'result.json').read_bytes()
        (directory / 'result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'inventory/hash'):
            verify_run(run.path)
        (directory / 'result.json').write_bytes(original)
        (directory / 'extra').write_text('not in sealed evidence')
        with self.assertRaisesRegex(ValueError, 'inventory/hash'):
            verify_run(run.path)

    def test_parent_rejects_success_exit_with_corrupt_raw_inventory(self):
        real_command = runner.run_command
        def corrupt(*args, **kwargs):
            result = real_command(*args, **kwargs)
            directory = Path(kwargs['stdout_path']).parent
            (directory / 'trajectory/protocol.jsonl').write_text('{}\n')
            return result
        with patch.object(runner, 'run_command', side_effect=corrupt):
            run = self.run_eval()
        self.assertEqual(run.summary['failed'], 1)
        self.assertEqual(run.summary['teamWins'], 0)
        self.assertEqual(verify_run(run.path)['technicalFailures'], 1)

    def test_symlink_output_and_artifacts_rejected(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.root / 'results').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.run_eval()
        self.assertEqual(list(elsewhere.iterdir()), [])
        (self.root / 'results').unlink()
        run = self.run_eval()
        target = run.path / 'summary.json'
        target.unlink()
        target.symlink_to(self.script)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            verify_run(run.path)

    def test_dirty_rules_rejected_before_run_creation(self):
        (self.root / 'game/untracked').write_text('changed rules')
        with self.assertRaisesRegex(ValueError, 'not pristine'):
            self.run_eval()
        self.assertFalse((self.root / 'results').exists())

    def test_replay_is_lazy_and_verifies_before_import(self):
        run = self.run_eval()
        fake_replay = type(sys)('arknights.replay')
        fake_replay.replay = lambda value, **kwargs: (value.path, kwargs)
        with patch.dict(sys.modules, {'arknights.replay': fake_replay}):
            self.assertEqual(run.replay('0001-original-s1', serve=False),
                             (run.path, {'episode': '0001-original-s1', 'serve': False}))
            (run.path / 'summary.json').write_text('{}')
            with self.assertRaises(ValueError):
                run.replay()


if __name__ == '__main__':
    unittest.main()
