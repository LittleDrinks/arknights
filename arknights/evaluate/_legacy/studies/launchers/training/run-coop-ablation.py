from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import os,json,subprocess,hashlib,datetime
root=Path('/data1/zsm/arknights');out=root/'experiments/coop-partner-ablation-v1';out.mkdir(exist_ok=True)
if (out/'manifest.json').exists():raise SystemExit('Refuse existing experiment')
clock=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat();sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
base=root/'experiments/bc-expansion-v1/all.pt';config=json.loads((root/'experiment-code/rl/config.json').read_text());config.update(gamma=1.0,lr=.0001,rollout_steps=64)
(out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
m={'kind':'normal vs heterogeneous frozen heuristic partners, not learned FCP','status':'running','startedAt':clock(),'checkpointSha256':sha(base),'poolManifest':json.loads((root/'experiment-code/coop/pool.json').read_text()),'poolManifestSha256':sha(root/'experiment-code/coop/pool.json'),'config':config,'primaryMetric':'ABYSS complete team summary.victory','plannedStepsPerArm':16384,'workers':4,'seedBase':50001,'seedSegments':[{'worker':i,'start':50001+i*4096,'end':50001+i*4096+511} for i in range(4)],'arms':[]}
def save():(out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
save()
def run(label):
 env={k:v for k,v in os.environ.items() if not k.startswith(('COOP_','ARK_'))};env.update(COOP_POOL=label,COOP_SPLIT='train',COOP_POOL_SEED='313',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
 if label=='mixed':env['COOP_TEAM']='original-v1,buy2-v1,saver-v1'
 cmd=[str(root/'venvs/learner-20261018/bin/python3'),str(root/'experiment-code/parallel-rl/train.py'),'--checkpoint',str(base),'--out',str(out/(label+'.pt')),'--config',str(out/'config.json'),'--workers','4','--steps','16384','--rollout','64','--seed','50001','--episodes-per-worker','512','--mode','coop','--difficulty','ABYSS','--env-command',f'node {root}/experiment-code/coop/env.mjs --game {root}/game','--reward-mode','terminal/v1','--provenance-file',str(root/'experiment-code/coop/pool.json')]
 rec={'arm':label,'environment':{k:v for k,v in env.items() if k.startswith('COOP_')},'command':cmd,'startedAt':clock()}
 with (out/(label+'.log')).open('w') as log:r=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
 rec.update(exitCode=r.returncode,finishedAt=clock());return rec
try:
 with ThreadPoolExecutor(max_workers=2) as pool:
  for f in as_completed([pool.submit(run,n) for n in ['normal','mixed']]):r=f.result();m['arms'].append(r);save();print(json.dumps(r),flush=True)
 m['checkpoints']={f.name:sha(f) for f in out.glob('*.pt')};m['status']='complete' if all(r['exitCode']==0 for r in m['arms']) else 'failed'
except Exception as e:m.update(status='failed',error=repr(e));raise
finally:m['finishedAt']=clock();save()
