"""Finish the already authorized partner cross-play study after both frozen training arms finish."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,os,subprocess,time
root=Path('/data1/zsm/arknights');folder=root/'experiments/coop-partner-ablation-v2';out=folder/'crossplay';out.mkdir(parents=True,exist_ok=True)
if (out/'manifest.json').exists():raise SystemExit('Refuse existing crossplay manifest')
clock=lambda:datetime.now(timezone.utc).isoformat();sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
m={'experiment':'frozen normal-vs-mixed partner training cross-play','status':'waiting_for_training','startedAt':clock(),'primaryMetric':'ABYSS complete team victory','plannedGames':128,'seedStart':1,'seedEnd':16,'mapSeedsAlreadyDevelopment':True,'heldoutFamily':'random-legal, absent from training pool','families':{},'results':[]}
def save():(out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
save();deadline=time.monotonic()+10800
try:
 while True:
  f=folder/'manifest.json';training=json.loads(f.read_text()) if f.exists() else {}
  statuses=[]
  for name in ['normal','mixed']:
   p=folder/(name+'.pt.manifest.json');d=json.loads(p.read_text()) if p.exists() else {};statuses.append(d.get('status','not_started'))
  m['trainingStatuses']=statuses;m['checkedAt']=clock();save()
  if 'failed' in statuses or training.get('status')=='failed':raise RuntimeError('Training arm failed; no cross-play of partial weights')
  if statuses==['complete','complete'] and training.get('status')=='complete':break
  if time.monotonic()>deadline:raise TimeoutError('Training did not complete within 3 hours')
  time.sleep(20)
 m['status']='evaluating';m['checkpointSha256']={n:sha(folder/(n+'.pt')) for n in ['normal','mixed']};save()
 families={'original':('normal','original-v1,original-v1,original-v1'),'weak-buy2':('weak','buy2-v1,buy2-v1,buy2-v1'),'weak-saver':('weak','saver-v1,saver-v1,saver-v1'),'heldout-random':('heldout','random-v1,random-v1,random-v1')}
 def run(label,pool,team):
  env={k:v for k,v in os.environ.items() if not k.startswith(('COOP_','ARK_'))};env.update(COOP_POOL=pool,COOP_SPLIT='eval',COOP_POOL_SEED='7919',COOP_TEAM=team)
  cmd=['python3',str(root/'tools/evaluate-models.py'),'--root',str(root),'--checkpoints',str(folder),'--names','normal','mixed','--out',str(out/label),'--difficulties','ABYSS','--parallel','2','--env-command',f'node {root}/experiment-code/coop/env.mjs --game {root}/game']
  with (out/(label+'.log')).open('w') as log:p=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
  return {'family':label,'pool':pool,'team':team,'exitCode':p.returncode,'command':cmd,'finishedAt':clock()}
 with ThreadPoolExecutor(max_workers=4) as pool:
  for f in as_completed([pool.submit(run,label,*values) for label,values in families.items()]):r=f.result();m['results'].append(r);save()
 if any(sha(folder/(n+'.pt'))!=h for n,h in m['checkpointSha256'].items()):raise RuntimeError('Candidate checkpoint changed during cross-play')
 m['status']='complete' if all(r['exitCode']==0 for r in m['results']) else 'failed'
except Exception as e:m.update(status='failed',error=repr(e))
finally:m['finishedAt']=clock();save()
