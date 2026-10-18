"""Paired terminal-only vs explicitly shaped training reward; engine victory remains primary."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,os
from pathlib import Path
import subprocess

root=Path('/data1/zsm/arknights');out=root/'experiments/reward-ablation-v1'
if (out/'manifest.json').exists():raise SystemExit('Refuse existing run manifest')
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
checkpoint=root/'experiments/learning-pilot-v2/bc.pt';config=out/'config.json'
sources=[*(root/'experiment-code/rl').glob('*.mjs'),root/'experiment-code/rl/learner.py',root/'experiment-code/reward/env.mjs',Path(__file__)]
hashes={str(f):sha(f) for f in sources}
clock=lambda:datetime.now(timezone.utc).isoformat()
manifest={'experiment':'training reward ablation','startedAt':clock(),'status':'running','checkpointSha256':sha(checkpoint),'configSha256':sha(config),'sources':hashes,'seedSplitSha256':sha(root/'experiments/seed-split-v1.json'),'plannedStepsPerArm':12288,'seed':30001,'curriculum':['NORMAL','HARD','ABYSS'],'primaryMetric':'ABYSS summary.victory in separate frozen evaluation','shapedReward':'gamma1; .02 per completed round plus .4 terminal boss damage on failure; privileged training-only; not potential-based invariant shaping','arms':[]}
def save():(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
save()
def run(label):
    envcmd=f'node {root}/experiment-code/'+('reward/env.mjs' if label=='shaped' else f'rl/env.mjs --game {root}/game')
    cmd=[str(root/'venvs/learner-20261018/bin/python3'),str(root/'experiment-code/rl/learner.py'),'--env-command',envcmd,'--config',str(config),'--seed','30001','ppo','--checkpoint',str(checkpoint),'--out',str(out/(label+'.pt')),'--steps-per-stage','4096','--curriculum',','.join(manifest['curriculum'])]
    env={k:v for k,v in os.environ.items() if not k.startswith(('ARK_STACKING','ARK_TEACHER','ARK_POLICY','ARK_ITEM_CONTROLLER'))};env.update(ARK_ROOT=str(root),OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    rec={'arm':label,'command':cmd,'startedAt':clock(),'environment':{k:env[k] for k in ['ARK_ROOT','OMP_NUM_THREADS','MKL_NUM_THREADS']}}
    with (out/(label+'.log')).open('w') as log:r=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
    rec.update(exitCode=r.returncode,finishedAt=clock());return rec
try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(run,n) for n in ['terminal','shaped']]):
            r=f.result();manifest['arms'].append(r);save();print(json.dumps(r),flush=True)
    if any(sha(Path(f))!=h for f,h in hashes.items()) or sha(checkpoint)!=manifest['checkpointSha256'] or sha(config)!=manifest['configSha256']:raise RuntimeError('Training inputs changed')
    manifest['checkpoints']={f.name:sha(f) for f in out.glob('*.pt')};manifest['status']='complete' if all(r['exitCode']==0 for r in manifest['arms']) else 'failed'
except Exception as e:manifest['status']='failed';manifest['error']=repr(e);raise
finally:manifest['finishedAt']=clock();save()
if manifest['status']!='complete':raise SystemExit(1)
