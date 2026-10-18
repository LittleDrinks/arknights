from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json,os,subprocess,hashlib
root=Path('/data1/zsm/arknights');out=root/'experiments/solo-scale-v1';out.mkdir(exist_ok=True)
if (out/'manifest.json').exists():raise SystemExit('Use new output')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();clock=lambda:datetime.now(timezone.utc).isoformat()
config=json.loads((root/'experiment-code/rl/config.json').read_text());config.update(gamma=1.,gae_lambda=.99,lr=.00003,clip=.1,epochs=3,entropy_coef=.005,batch_size=128);(out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
cp={n:root/('experiments/bc-expansion-v1/'+n+'.pt') for n in ['all','wins']}
m={'kind':'2x2 BC training-data source vs terminal/shaped PPO reward','status':'running','startedAt':clock(),'config':config,'stepsPerArm':65536,'workersPerArm':8,'parallelArms':4,'seedBase':20001,'episodesPerWorker':512,'checkpointSha256':{k:sha(v) for k,v in cp.items()},'rules':'6ab2468279e926d107788f63d7fe219b85f67b4a','primaryMetric':'ABYSS full summary.victory, frozen separate evaluation','shaping':'privileged terminal boss reward and round progress, not a success metric','arms':[]}
def save():(out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
save()
def run(data,reward):
 label=data+'-'+reward;env={k:v for k,v in os.environ.items() if not k.startswith(('COOP_','ARK_'))};env.update(ARK_ROOT=str(root),OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
 envcmd=f'node {root}/experiment-code/'+('reward/env.mjs' if reward=='shaped' else f'rl/env.mjs --game {root}/game')
 cmd=[str(root/'venvs/learner-20261018/bin/python3'),str(root/'experiment-code/parallel-rl/train.py'),'--checkpoint',str(cp[data]),'--out',str(out/(label+'.pt')),'--config',str(out/'config.json'),'--workers','8','--steps','65536','--rollout','128','--seed','20001','--episodes-per-worker','512','--difficulty','ABYSS','--env-command',envcmd,'--reward-mode','round-progress+terminal-boss/v1' if reward=='shaped' else 'terminal/v1','--provenance-file',str(root/'experiments/seed-split-v1.json')]
 rec={'arm':label,'command':cmd,'startedAt':clock()}
 with (out/(label+'.log')).open('w') as log:p=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
 rec.update(exitCode=p.returncode,finishedAt=clock());return rec
try:
 with ThreadPoolExecutor(max_workers=4) as pool:
  for f in as_completed([pool.submit(run,d,r) for d in cp for r in ['terminal','shaped']]):x=f.result();m['arms'].append(x);save();print(json.dumps(x),flush=True)
 m['status']='complete' if all(x['exitCode']==0 for x in m['arms']) else 'failed'
except Exception as e:m.update(status='failed',error=repr(e));raise
finally:m['finishedAt']=clock();save()
