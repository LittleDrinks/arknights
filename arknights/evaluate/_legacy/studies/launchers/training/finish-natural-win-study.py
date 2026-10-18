"""Finite continuation: only complete frozen natural-win BC weights enter dev evaluation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

root = Path('/data1/zsm/arknights')
out = root / 'experiments/natural-win-study-v3'
out.mkdir(exist_ok=False)
training = root / 'experiments/natural-win-bc-v3'
evaluation = root / 'experiments/natural-win-bc-v3-dev-eval'
clock = lambda: datetime.now(timezone.utc).isoformat()
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
report = dict(status='waiting-for-complete-training', startedAt=clock(), plannedGames=64,
              kind='same-parent natural personal-win BC versus parent, development only',
              primaryMetric='complete ABYSS team victory; individual survival separately',
              seeds=[1, 16], families=['original', 'mixed'], validationUsed=False)
def save():
    temp = out / 'manifest.json.tmp'
    temp.write_text(json.dumps(report, indent=2) + '\n')
    temp.replace(out / 'manifest.json')
save()
try:
    deadline = time.monotonic() + 1500
    while time.monotonic() < deadline:
        file = training / 'train.json'
        if file.exists():
            train = json.loads(file.read_text())
            if train['status'] in ('complete', 'failed'):
                break
        time.sleep(10)
    else:
        raise TimeoutError('Complete training not available within finite wait')
    assert train['status'] == 'complete', train.get('error', 'Training incomplete')
    assert train['positiveGames'] == 6 and train['positiveLabels'] == 498
    assert train['result']['actualUpdates'] == 200 and train['result']['actualDraws'] == 6800
    checkpoint = training / 'naturalWins.pt'
    assert sha(checkpoint) == train['result']['checkpointSHA256']
    report.update(status='evaluating', checkpointSHA256=sha(checkpoint), trainingManifestSHA256=sha(file))
    command = [str(root / 'venvs/learner-20261018/bin/python3'), '-B',
               str(root / 'experiment-code/natural-win-bc/run.py'), 'eval', '--dev-only', '--root', str(root),
               '--trained', str(training), '--parent', 'experiments/bc-expansion-v1/all.pt',
               '--parent-sha256', 'f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a',
               '--out', str(evaluation), '--workers', '4', '--timeout', '120', '--total-timeout', '1100', '--output-mib', '64']
    report['command'] = command
    save()
    with (root / 'logs/natural-win-bc-v3-dev-eval.log').open('x') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    summary = json.loads((evaluation / 'summary.json').read_text())
    report.update(evaluationExitCode=result.returncode, summarySHA256=sha(evaluation / 'summary.json'),
                  summary={k:v for k,v in summary.items() if k != 'games'})
    assert result.returncode == 0 and summary['status'] == 'complete', 'Development evaluation incomplete'
    assert sha(checkpoint) == report['checkpointSHA256'] and sha(file) == report['trainingManifestSHA256']
    report['status'] = 'complete'
except BaseException as error:
    report.update(status='failed', error=repr(error))
    raise
finally:
    report['finishedAt'] = clock()
    save()
