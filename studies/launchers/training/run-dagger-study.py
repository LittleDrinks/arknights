from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,os,subprocess
root=Path('/data1/zsm/arknights');out=root/'experiments/dagger-study-v1';out.mkdir(exist_ok=True)
if (out/'manifest.json').exists():raise SystemExit('Refuse existing study')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();clock=lambda:datetime.now(timezone.utc).isoformat()
cp=root/'experiments/bc-expansion-v1/all.pt';base=root/'experiments/bc-expansion-v1/all.jsonl';python=root/'venvs/learner-20261018/bin/python3';run=root/'experiment-code/dagger/run.py'
env={**os.environ,'DAGGER_OUTPUT_ROOT':str(root),'STRONGHOLD_GAME':str(root/'game'),'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
manifest={'kind':'current-state weak heuristic teacher DAgger ablation','status':'running','startedAt':clock(),'checkpointSha256':sha(cp),'teacherSha256':sha(root/'experiment-code/dagger/teacher.mjs'),'sourceHashes':{str(f):sha(f) for f in (root/'experiment-code/dagger').glob('*') if f.is_file()},'teacherNotExpert':True,'safety':'off','betas':{'model':'0','mixed':'0.5','teacher':'1'},'plannedCollections':72,'collectionSeeds':[70001,70008],'difficulties':['NORMAL','HARD','ABYSS'],'collections':[],'training':[]}
def save():(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
save()
def collect(label,beta,difficulty,seed):
 dest=out/f'{label}-{difficulty}-{seed}.jsonl.gz';cmd=[str(python),str(run),'collect','--checkpoint',str(cp),'--seed',str(seed),'--episodes','1','--difficulty',difficulty,'--beta',beta,'--out',str(dest)]
 with (out/f'{label}-{difficulty}-{seed}.log').open('w') as log:p=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
 record={'arm':label,'beta':beta,'difficulty':difficulty,'seed':seed,'exitCode':p.returncode,'file':str(dest),'command':cmd,'status':'invalid'}
 if p.returncode==0:
  m=json.loads(Path(str(dest)+'.manifest.json').read_text());eps=m.get('episodes',[])
  if len(eps)==1 and eps[0]['done'] and not any(eps[0][k] for k in ['engineErrors','actionErrors','controllerErrors']):record.update(status='complete',victory=eps[0]['victory'],steps=eps[0]['steps'],modelSteps=eps[0]['modelSteps'],teacherSteps=eps[0]['teacherSteps'],sha256=sha(dest))
 return record
try:
 with ThreadPoolExecutor(max_workers=6) as pool:
  futures=[pool.submit(collect,label,beta,d,s) for label,beta in manifest['betas'].items() for d in manifest['difficulties'] for s in range(70001,70009)]
  for f in as_completed(futures):r=f.result();manifest['collections'].append(r);save()
 def train(label):
  valid=sorted([r['file'] for r in manifest['collections'] if r['arm']==label and r['status']=='complete']);cmd=[str(python),str(run),'train','--checkpoint',str(cp),'--seed','70301','--max-samples','512','--epochs','6','--batch-size','64','--lr','0.0001','--existing-bc',str(base),'--out',str(out/(label+'.pt'))]
  for filename in valid:cmd+=['--data',filename]
  record={'arm':label,'validCollections':len(valid),'invalidCollections':24-len(valid),'plannedCollections':24,'command':cmd}
  if not valid:return {**record,'status':'failed','error':'No valid collection'}
  with (out/(label+'-train.log')).open('w') as log:p=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
  return {**record,'status':'complete' if p.returncode==0 else 'failed','exitCode':p.returncode,'checkpointSha256':sha(out/(label+'.pt')) if (out/(label+'.pt')).exists() else None}
 with ThreadPoolExecutor(max_workers=3) as pool:
  for f in as_completed([pool.submit(train,label) for label in manifest['betas']]):r=f.result();manifest['training'].append(r);save();print(json.dumps({k:v for k,v in r.items() if k!='command'}),flush=True)
 manifest['status']='complete_with_invalid_collections' if any(r['status']!='complete' for r in manifest['collections']) else 'complete'
 if any(r['status']!='complete' for r in manifest['training']):manifest['status']='failed'
except Exception as e:manifest.update(status='failed',error=repr(e));raise
finally:manifest['finishedAt']=clock();save()
