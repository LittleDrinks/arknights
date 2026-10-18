"""Evaluate frozen model files on a declared seed block, retaining failures and exact action traces."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True)
p.add_argument('--checkpoints',type=Path,required=True)
p.add_argument('--names',nargs='+',required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--split',choices=['developmentRegression','validation'],default='developmentRegression')
p.add_argument('--difficulties',nargs='+',default=['NORMAL','ABYSS'])
p.add_argument('--parallel',type=int,default=3)
p.add_argument('--env-command',default=None,help='Optional declared controller/coop environment ablation')
a=p.parse_args();root=a.root.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
if (out/'manifest.json').exists():raise SystemExit('Use a new evaluation output directory')
if any(d not in ['NORMAL','HARD','ABYSS'] for d in a.difficulties) or len(set(a.difficulties))!=len(a.difficulties):raise SystemExit('Invalid difficulty list')
if len(set(a.names))!=len(a.names):raise SystemExit('Repeated candidate')
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
splitfile=root/'experiments/seed-split-v1.json';split=json.loads(splitfile.read_text())[a.split]
first,last=split['start'],split['end'];episodes=last-first+1
files={n:(a.checkpoints/(n+'.pt')).resolve() for n in a.names};hashes={n:sha(f) for n,f in files.items()}
clock=lambda:datetime.now(timezone.utc).isoformat()
manifest={'kind':'frozen model evaluation','status':'running','split':a.split,'seedStart':first,'seedEnd':last,'plannedGames':episodes*len(files)*len(a.difficulties),'checkpointSha256':hashes,'seedSplitSha256':sha(splitfile),'startedAt':clock(),'arms':[]}
manifest['environment']={k:os.environ.get(k) for k in ['COOP_POOL','COOP_SPLIT','COOP_POOL_SEED','COOP_TEAM','ARK_ITEM_CONTROLLER','ARK_ROOT','STRONGHOLD_GAME']}
source_dirs=['rl','coop','controller-v2','reward']
source_hashes={str(f):sha(f) for directory in source_dirs for f in (root/'experiment-code'/directory).glob('*') if f.is_file() and f.suffix in ['.mjs','.py','.json']}
manifest['sourceHashes']=source_hashes
def save():(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
save()
def run(name,difficulty):
    label=name+'-'+difficulty;trace=out/(label+'-traces');logfile=out/(label+'.jsonl')
    envcommand=a.env_command or f'node {root}/experiment-code/rl/env.mjs --game {root}/game'
    cmd=[str(root/'venvs/learner-20261018/bin/python3'),str(root/'experiment-code/rl/learner.py'),'--env-command',envcommand,'--seed',str(first),'eval','--checkpoint',str(files[name]),'--episodes',str(episodes),'--difficulty',difficulty,'--trace-dir',str(trace)]
    rec={'name':name,'difficulty':difficulty,'plannedGames':episodes,'command':cmd,'status':'failed','startedAt':clock()}
    try:
        with logfile.open('w') as log:r=subprocess.run(cmd,env={**os.environ,'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'},stdout=log,stderr=subprocess.STDOUT)
        rec['exitCode']=r.returncode
        rows=[]
        for line in logfile.read_text().splitlines():
            try:row=json.loads(line)
            except json.JSONDecodeError:continue
            if 'seed' in row and 'victory' in row:rows.append(row)
        rec['completedGames']=len(rows);rec['wins']=sum(row['victory'] is True and row['engineErrors']==0 and row['actionErrors']==0 for row in rows)
        rec['winRateOnPlanned']=rec['wins']/episodes
        rec['engineErrors']=sum(row['engineErrors'] for row in rows);rec['actionErrors']=sum(row['actionErrors'] for row in rows)
        rec['controllerRejectedCalls']=sum(row['controllerErrors'] for row in rows)
        rec['forcedReady']=sum(row['budgetForcedReady'] for row in rows)
        rec['meanRounds']=sum(row['rounds'] for row in rows)/len(rows) if rows else None
        if r.returncode or [row['seed'] for row in rows]!=list(range(first,last+1)) or any(row['difficulty']!=difficulty or type(row['victory']) is not bool for row in rows) or rec['engineErrors'] or rec['actionErrors']:raise RuntimeError('Incomplete/invalid evaluation')
        if sha(files[name])!=hashes[name]:raise RuntimeError('Checkpoint changed during evaluation')
        rec['traces']={f.name:sha(f) for f in trace.glob('*.json')}
        if len(rec['traces'])!=episodes:raise RuntimeError('Missing complete action traces')
        rec['status']='complete'
    except Exception as e:rec['error']=repr(e)
    rec['finishedAt']=clock();return rec
try:
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        for future in as_completed([pool.submit(run,n,d) for n in files for d in a.difficulties]):
            rec=future.result();manifest['arms'].append(rec);save();print(json.dumps({k:v for k,v in rec.items() if k!='traces'}),flush=True)
    if sha(splitfile)!=manifest['seedSplitSha256']:raise RuntimeError('Split changed during evaluation')
    if any(sha(Path(f))!=h for f,h in source_hashes.items()):raise RuntimeError('Environment sources changed during evaluation')
    manifest['status']='complete' if all(r['status']=='complete' for r in manifest['arms']) else 'failed'
except Exception as e:manifest['status']='failed';manifest['error']=repr(e)
finally:manifest['finishedAt']=clock();save()
if manifest['status']!='complete':raise SystemExit(1)
