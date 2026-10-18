from pathlib import Path
import os,json,gzip,sys,hashlib
r=Path('/data1/zsm/arknights');sys.path.insert(0,str(r/'experiment-code'))
os.environ.update(COOP_POOL='normal',COOP_SPLIT='train',COOP_POOL_SEED='313')
os.environ.pop('COOP_TEAM',None)
from rl.learner import Environment
out=r/'experiments/coop-original-winner-control-v1';out.mkdir(exist_ok=False)
m={'status':'running','plannedSeeds':[50003,50026],'kind':'four original bots at reported training-winning seeds, diagnostic only','results':[]}
def save():(out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
save()
try:
 for seed in m['plannedSeeds']:
  env=Environment(f'node {r}/experiment-code/coop/env.mjs --game {r}/game',timeout=120)
  try:
   d=env.request('demo',seed=seed,difficulty='ABYSS',budget=128)
   t=d['terminal'];assert t['done'] and t['summary']['seed']==seed
   assert t['summary']['errors']==0 and t['engineErrors']==0 and t['actionErrors']==0
   p=out/(str(seed)+'.json.gz')
   with gzip.open(p,'wt') as f:json.dump(d,f)
   rec={'seed':seed,'victory':t['victory'],'rounds':t['rounds'],'primaryVictory':t['summary']['players'][0]['victory'],'primaryRounds':t['summary']['players'][0]['roundsPassed'],'samples':len(d['samples']),'rejectedLabels':d['rejectedLabels'],'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
   m['results'].append(rec);save();print(json.dumps(rec),flush=True)
  except Exception as e:m['results'].append({'seed':seed,'error':repr(e)});save()
  finally:env.close()
 m['status']='complete' if len(m['results'])==2 and not any('error' in x for x in m['results']) else 'failed'
finally:save()
