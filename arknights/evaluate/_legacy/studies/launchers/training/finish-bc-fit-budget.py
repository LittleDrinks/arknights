"""Finite continuation: only complete, frozen two-budget BC enters the 96-game dev plan."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

root = Path('/data1/zsm/arknights')
os.sched_setaffinity(0, {4, 5, 6, 7})
out = root / 'experiments/bc-fit-budget-study-v1'
out.mkdir(exist_ok=False)
training = root / 'experiments/bc-fit-budget-train-v1'
evaluation = root / 'experiments/bc-fit-budget-dev-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
clock = lambda: datetime.now(timezone.utc).isoformat()
report = dict(status='waiting-for-complete-training', startedAt=clock(), plannedGames=96,
              kind='parent versus 1000/3000 Adam steps on six natural personal-win demonstrations',
              metric='complete ABYSS team victory; individual survival separately',
              seedGroups=16, pools=['original','mixed'], validationUsed=False, cpuAffinity=sorted(os.sched_getaffinity(0)))
def save():
    temp = out / 'manifest.json.tmp'
    temp.write_text(json.dumps(report, indent=2)+'\n')
    temp.replace(out / 'manifest.json')
save()
try:
    deadline = time.monotonic()+1200
    while time.monotonic()<deadline:
        file = training / 'train.json'
        if file.exists():
            train = json.loads(file.read_bytes())
            if train['status'] in ('complete','failed'):
                break
        time.sleep(5)
    else:
        raise TimeoutError('Complete training not available within finite wait')
    assert train['status']=='complete', train.get('error','Training incomplete')
    assert train['positiveGames']==6 and train['positiveLabels']==498
    hashes = {}
    for name, steps in [('steps1000',1000),('steps3000',3000)]:
        record = train['models'][name]
        assert record['actualUpdates']==steps and record['actualDraws']==steps*34
        hashes[name] = sha(training / (name+'.pt'))
        assert hashes[name]==record['checkpointSHA256']
    report.update(status='evaluating', checkpointSHA256=hashes, trainingManifestSHA256=sha(file))
    command = [str(root / 'venvs/learner-20261018/bin/python'),'-B',
               str(root / 'experiment-code/bc-fit-budget/run.py'),'eval','--dev-only',
               '--root',str(root),'--experiment-code',str(root / 'experiment-code'),
               '--trained',str(training),'--out',str(evaluation),'--workers','4','--timeout','120',
               '--total-timeout','1100','--output-mib','64']
    report['command']=command
    save()
    with (root / 'logs/bc-fit-budget-dev-v1.log').open('x') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,timeout=1200)
    summary = json.loads((evaluation / 'summary.json').read_bytes())
    report.update(evaluationExitCode=result.returncode,summarySHA256=sha(evaluation / 'summary.json'),
                  summary={k:v for k,v in summary.items() if k not in ('games','source','game')})
    assert result.returncode==0 and summary['status']=='complete' and summary['creditGranted'] is True
    assert all(sha(training / (n+'.pt'))==h for n,h in hashes.items())
    assert sha(file)==report['trainingManifestSHA256']
    report['status']='complete'
except BaseException as error:
    report.update(status='failed', error=repr(error))
    raise
finally:
    report['finishedAt']=clock()
    save()
