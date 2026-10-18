"""Replay the original first 21 mixed-partner PPO updates and retain real trajectories."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

root = Path('/data1/zsm/arknights')
out = root / 'experiments/coop-mixed-prefix-capture-v1'
out.mkdir(exist_ok=False)
original = root / 'experiments/coop-partner-ablation-v3'
original_manifest = json.loads((original / 'manifest.json').read_text())
command = list(next(x['command'] for x in original_manifest['training'] if x['arm'] == 'mixed'))
def replace(flag, value):
    command[command.index(flag) + 1] = str(value)
replace('--steps', 5376)
replace('--out', out / 'mixed.pt')
environment = command[command.index('--env-command') + 1]
replace('--env-command', shlex.join([str(root / 'venvs/learner-20261018/bin/python3'),
    str(root / 'tools/capture-env.py'), '--out', str(out / 'episodes'), '--', *shlex.split(environment)]))
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
sources = dict(original_manifest['sourceSHA256'])
# The original orchestration script itself is historical; only compare the trainer's declared
# executable sources and all current game rule files, not an unexecuted old launcher.
old_train = json.loads((original / 'mixed.pt.manifest.json').read_text())
for name, identity in old_train['sourceSHA256'].items():
    path = Path(name)
    if not path.is_absolute():
        path = root / 'experiment-code' / name
    assert sha(path) == identity, f'Executable source drift: {path}'
parent = root / 'experiments/bc-expansion-v1/all.pt'
assert sha(parent) == old_train['checkpointInput']['sha256']
manifest = dict(status='running', command=command, seed=50026, purpose='Exact historical training-prefix reproduction, not frozen evaluation',
                plannedUpdates=21, plannedSteps=5376, originalTrainingSHA256=sha(original / 'mixed.pt.jsonl'),
                parentSHA256=sha(parent), sourceSHA256=old_train['sourceSHA256'], captureSourceSHA256=sha(root / 'tools/capture-env.py'),
                startedAt=datetime.now(timezone.utc).isoformat())
def save():
    temp = out / 'manifest.json.tmp'
    temp.write_text(json.dumps(manifest, indent=2) + '\n')
    temp.replace(out / 'manifest.json')
save()
env = {k:v for k,v in os.environ.items() if not k.startswith(('ARK_', 'COOP_'))}
env.update(COOP_POOL='mixed', COOP_SPLIT='train', COOP_POOL_SEED='313', COOP_TEAM='original-v1,buy2-v1,saver-v1',
           STRONGHOLD_GAME=str(root / 'game'), OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
try:
    with (out / 'train.log').open('x') as log:
        run = subprocess.run(command, cwd=root / 'experiment-code', env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    assert run.returncode == 0, f'training exit={run.returncode}'
    reference = [json.loads(line) for line in (original / 'mixed.pt.jsonl').open()][:21]
    actual = [json.loads(line) for line in (out / 'mixed.pt.jsonl').open()]
    assert len(reference) == len(actual) == 21
    # Compare entire recorded per-update behavior, weights and episode results except runtime.
    comparisons = []
    for index, (a,b) in enumerate(zip(reference,actual),1):
        fields = ['behaviorSHA256', 'parameterSHA256', 'episodes', 'totalSteps', 'workerSteps']
        assert all(k in a and k in b for k in fields), (index, a.keys())
        assert all(a[k] == b[k] for k in fields), f'Prefix divergence update {index}'
        comparisons.append(dict(update=index, behaviorSHA256=b['behaviorSHA256'], parameterSHA256=b['parameterSHA256']))
    winners = []
    for p in (out / 'episodes').glob('*.json'):
        e = json.loads(p.read_text())
        if e['terminal']['victory']:
            winners.append(dict(path=str(p), sha256=sha(p), seed=e['terminal']['episode']['seed'],
                                primaryIndividualVictory=e['terminal']['summary']['players'][0]['victory']))
    assert any(w['seed'] == 50026 for w in winners), 'Expected mixed success absent'
    manifest.update(status='complete', exactPrefix=True, comparisons=comparisons, winners=winners)
except BaseException as error:
    manifest.update(status='failed', error=repr(error))
    raise
finally:
    manifest['finishedAt'] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({k:v for k,v in manifest.items() if k not in ['sourceSHA256','command','comparisons']}))
