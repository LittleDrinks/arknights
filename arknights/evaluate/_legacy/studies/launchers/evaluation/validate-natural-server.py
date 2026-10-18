"""Run the unchanged synthetic suite with an external race-safe directory monitor."""
from pathlib import Path
import hashlib
import json
import subprocess
import time

root = Path('/data1/zsm/arknights')
original = root / 'experiment-code/natural-win-bc/check.py'
raw = original.read_text()
needle = """                for p in Path(tmp).rglob('*'):
                    try:
                        if p.is_file():
                            size += p.stat().st_size
                    except FileNotFoundError:
                        pass"""
replacement = """                for directory, _, files in os.walk(tmp):
                    for name in files:
                        try:
                            size += (Path(directory) / name).stat().st_size
                        except FileNotFoundError:
                            pass"""
assert raw.count(needle) == 1
fixed = raw.replace(needle, replacement).replace('here = Path(__file__).resolve().parent',
                                               'here = Path(' + repr(str(original.parent)) + ')')
target = root / 'tools/natural-test-supervisor-v2.py'
with target.open('x') as stream:
    stream.write(fixed)
started = time.monotonic()
result = subprocess.run([str(root / 'venvs/learner-20261018/bin/python3'), '-B', str(target)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
receipt = dict(exitCode=result.returncode, seconds=time.monotonic() - started,
               originalSupervisorSHA256=sha(original), externalSupervisorSHA256=sha(target),
               testsSHA256=sha(original.parent / 'tests.py'),
               purpose='Unchanged server synthetic suite; fix temporary-directory scan race externally, preserve active training sources',
               output=result.stdout)
with (root / 'experiments/natural-win-bc-server-tests-v2.json').open('x') as stream:
    json.dump(receipt, stream, indent=2)
print(result.stdout)
raise SystemExit(result.returncode)
