"""Finite continuation: evaluate only the two completed frozen BC arms."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

root = Path('/data1/zsm/arknights')
code = root / 'experiment-code/interaction'
training = code / 'server-paired-16k-v1'
out = root / 'experiments/interaction-study-v1'
out.mkdir(exist_ok=False)
clock = lambda: datetime.now(timezone.utc).isoformat()
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
manifest = dict(status='waiting_for_training', startedAt=clock(), plannedGames=64,
                split='development seeds1..16 x NORMAL/ABYSS x two architectures',
                primaryMetric='complete ABYSS victory', validationNotUsed=True)


def save():
    temporary = out / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(out / 'manifest.json')


save()
try:
    deadline = time.monotonic() + 10800
    while True:
        file = training / 'train.json'
        status = json.loads(file.read_text()) if file.exists() else {}
        manifest.update(trainingStatus=status.get('status', 'not_started'), checkedAt=clock())
        save()
        if status.get('status') == 'failed':
            raise RuntimeError('Architecture training failed; partial weights are not evaluated')
        if status.get('status') == 'complete':
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Training did not complete in three hours')
        time.sleep(20)
    names = ('concat-baseline', 'interaction')
    assert len(status['arms']) == 2
    checkpoints = [training / (name + '.pt') for name in names]
    hashes = {name: sha(file) for name, file in zip(names, checkpoints)}
    assert all(hashes[row['architecture']] == row['checkpointSHA256'] for row in status['arms'])
    command = [str(root / 'venvs/learner-20261018/bin/python3'), str(code / 'eval.py')]
    for file in checkpoints:
        command += ['--checkpoint', str(file)]
    command += ['--game', str(root / 'game'), '--out', str(code / 'server-dev-eval-v1'),
                '--wall-seconds', '1800', '--max-output-bytes', '600000000']
    manifest.update(status='evaluating', checkpointSHA256=hashes, command=command,
                    trainingManifestSHA256=sha(training / 'train.json'))
    save()
    with (out / 'evaluation.log').open('w') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=1900,
                                env={**os.environ, 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
                                     'OPENBLAS_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    assert all(sha(file) == hashes[name] for name, file in zip(names, checkpoints))
    report = json.loads((code / 'server-dev-eval-v1/eval.json').read_text())
    manifest.update(status='complete' if result.returncode == 0 and report['status'] == 'complete' else 'failed',
                    exitCode=result.returncode, groups=report['groups'],
                    evalManifestSHA256=sha(code / 'server-dev-eval-v1/eval.json'))
except Exception as error:
    manifest.update(status='failed', error=repr(error))
finally:
    manifest['finishedAt'] = clock()
    save()
