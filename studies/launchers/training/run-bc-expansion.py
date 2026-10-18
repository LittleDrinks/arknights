from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import json,os,subprocess,hashlib,itertools,datetime
root=Path('/data1/zsm/arknights');out=root/'experiments/bc-expansion-v1';out.mkdir(exist_ok=True)
if (out/'manifest.json').exists():raise SystemExit('Refuse existing run')
source=root/'experiments/learning-pilot-v2';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();clock=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
config=json.loads((root/'experiment-code/rl/config.json').read_text());config.update(hidden=128,lr=.0005,batch_size=64);(out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
manifest={'kind':'BC dataset-quality ablation; same network/optimizer/epochs','status':'running','startedAt':clock(),'sourceCheckpoint':None,'teacher':'upstream weak bot with wins only NORMAL/HARD','epochs':24,'config':config,'sourceSha256':{d:sha(source/(d+'.jsonl')) for d in ['NORMAL','HARD','ABYSS']},'arms':[]}
def save():(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
save();counts={'all':0,'wins':0};epcounts={'all':0,'wins':0}
streams=[(source/(d+'.jsonl')).open() for d in ['NORMAL','HARD','ABYSS']];headers=[json.loads(next(s)) for s in streams]
assert all(h['schema']==headers[0]['schema'] and h['rules']==headers[0]['rules'] for h in headers)
with (out/'all.jsonl').open('w') as full,(out/'wins.jsonl').open('w') as wins:
 for f in [full,wins]:f.write(json.dumps({**headers[0],'sourceSha256':manifest['sourceSha256']})+'\n')
 for group in itertools.zip_longest(*streams):
  for line in group:
   if not line:continue
   row=json.loads(line);full.write(line)
   if row['type']=='sample':counts['all']+=1
   elif row['type']=='episode':epcounts['all']+=1
   if row.get('victory') is True:
    wins.write(line)
    if row['type']=='sample':counts['wins']+=1
    elif row['type']=='episode':epcounts['wins']+=1
for f in streams:f.close()
manifest.update(sampleCounts=counts,episodeCounts=epcounts);save()
def run(label):
 cmd=[str(root/'venvs/learner-20261018/bin/python3'),str(root/'experiment-code/rl/learner.py'),'--config',str(out/'config.json'),'--seed','101','bc','--data',str(out/(label+'.jsonl')),'--epochs','24','--max-samples',str(counts[label]),'--out',str(out/(label+'.pt'))]
 with (out/(label+'.log')).open('w') as log:r=subprocess.run(cmd,env={**os.environ,'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'},stdout=log,stderr=subprocess.STDOUT)
 return {'arm':label,'command':cmd,'exitCode':r.returncode,'checkpointSha256':sha(out/(label+'.pt')) if (out/(label+'.pt')).exists() else None,'finishedAt':clock()}
try:
 with ThreadPoolExecutor(max_workers=2) as pool:
  for f in as_completed([pool.submit(run,x) for x in counts]):
   r=f.result();manifest['arms'].append(r);save();print(json.dumps(r),flush=True)
 manifest['status']='complete' if all(r['exitCode']==0 for r in manifest['arms']) else 'failed'
except Exception as e:manifest.update(status='failed',error=repr(e));raise
finally:manifest['finishedAt']=clock();save()
