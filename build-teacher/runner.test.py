"""No Match or subprocess starts: fault-inject timeout and spawn failure."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('build_runner', Path(__file__).with_name('run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TimedOutChild:
    pid = 12345678
    returncode = -9

    def __init__(self, *args, **kwargs):
        # The full plan is persisted before the process could start.
        folder = Path(kwargs['stdout'].name).parent
        assert json.loads((folder / 'plan.json').read_text())['plannedGames'] == 4

    def poll(self):
        return None

    def wait(self):
        return self.returncode


for spawn_error in (False, True):
    with tempfile.TemporaryDirectory(prefix='build-plan-test-') as folder:
        clock = iter(range(100))
        argv = ['run.py', '--server', '--cases', 'upstream,off', '--seeds', '2', '--seconds', '1', '--out', 'fault']
        with patch.object(runner, 'HERE', Path(folder)), patch.object(runner, 'preflight'), \
             patch.object(sys, 'argv', argv), patch.object(runner.time, 'monotonic', side_effect=lambda: next(clock)), \
             patch.object(runner.subprocess, 'Popen', side_effect=OSError('spawn-fault') if spawn_error else TimedOutChild), \
             patch.object(runner, 'rss', return_value=0), patch.object(runner.os, 'killpg'), contextlib.redirect_stdout(io.StringIO()):
            try:
                runner.main()
            except (SystemExit, OSError) as error:
                assert (spawn_error and isinstance(error, OSError)) or (not spawn_error and error.code == 1)
            else:
                raise AssertionError('Fault must fail the run')
        output = Path(folder) / 'fault'
        plan, ledger = [json.loads((output / name).read_text()) for name in ('plan.json', 'budget.json')]
        assert plan['plannedGames'] == ledger['plannedGames'] == 4
        assert len(plan['records']) == len(ledger['records']) == 4
        assert [(r['case'], r['seed']) for r in ledger['records']] == [('upstream', 1), ('upstream', 2), ('off', 1), ('off', 2)]
        if not spawn_error:
            assert [r['status'] for r in ledger['records']] == ['timeout'] + ['not-run-budget'] * 3
            summary = json.loads((output / 'summary.json').read_text())
            assert len(summary['cases']) == 4 and all(r['victory'] is None for r in summary['cases'])
        else:
            assert [r['status'] for r in ledger['records']] == ['running'] + ['not-run'] * 3
        assert not list(output.glob('*.tmp'))
print('PASS: timeout and spawn failure retain all four planned games; no simulation')
