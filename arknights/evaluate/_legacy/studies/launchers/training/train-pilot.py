"""Server-only genuine demonstration/BC/PPO pilot, with immutable inputs and complete logs."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--episodes',type=int,default=64)
p.add_argument('--steps-per-stage',type=int,default=4096)
a=p.parse_args();root=a.root.resolve();out=a.out.resolve()
if not 1<=a.episodes<=64 or a.steps_per_stage<1:raise SystemExit('Invalid sampling budget')
out.mkdir(parents=True,exist_ok=True)
if (out/'manifest.json').exists():raise SystemExit('Use a fresh output directory')
python=root/'venvs/learner-20261018/bin/python3';lab=root/'experiment-code';learner=lab/'rl/learner.py'
envcmd=f'node {lab}/rl/env.mjs --game {root}/game'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
stamp=lambda:datetime.now(timezone.utc).isoformat()
sources={str(f):sha(f) for f in (lab/'rl').glob('*') if f.is_file()}
split=root/'experiments/seed-split-v1.json'
manifest={'kind':'real solo shop policy training pilot','codeRevision':'4f23f97','ruleCommit':'6ab2468279e926d107788f63d7fe219b85f67b4a','startedAt':stamp(),'status':'running','sourceHashes':sources,'seedSplitSha256':sha(split),'teacher':'upstream bot, weak; contains failed full episodes','plannedDemoEpisodes':a.episodes*3,'ppoStepsPerStage':a.steps_per_stage,'jobs':[]}
def save():(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
save()
def run(name,args,config=None):
    cmd=[str(python),str(learner),'--env-command',envcmd]
    if config:cmd+=['--config',str(config)]
    cmd+=args;record={'name':name,'command':cmd,'startedAt':stamp()}
    with (out/(name+'.log')).open('w') as log:
        proc=subprocess.run(cmd,env={**os.environ,'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'},cwd=lab,stdout=log,stderr=subprocess.STDOUT)
    record.update(exitCode=proc.returncode,finishedAt=stamp());return record
try:
    demos=[out/(d+'.jsonl') for d in ['NORMAL','HARD','ABYSS']]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(run,'demo-'+d,['--seed','10001','demo','--episodes',str(a.episodes),'--difficulty',d,'--out',str(f)]) for d,f in zip(['NORMAL','HARD','ABYSS'],demos)]
        for future in as_completed(futures):
            rec=future.result();manifest['jobs'].append(rec);save();print(json.dumps(rec),flush=True)
    if any(r['exitCode'] for r in manifest['jobs']):raise RuntimeError('Demonstration collection failed; partial files retained')
    streams=[f.open() for f in demos];headers=[json.loads(next(s)) for s in streams]
    if any(h['schema']!=headers[0]['schema'] or h['rules']!=headers[0]['rules'] for h in headers):raise RuntimeError('Mixed demonstration schemas/rules')
    combined=out/'balanced-demo.jsonl';samples=0;episodes=[]
    try:
        with combined.open('w') as dest:
            header={**headers[0],'sourceFiles':{f.name:sha(f) for f in demos},'sampling':'round-robin records, first 8192 samples; all episode outcomes preserved'}
            dest.write(json.dumps(header)+'\n')
            for group in itertools.zip_longest(*streams):
                for line in group:
                    if not line:continue
                    row=json.loads(line)
                    if row['type']=='sample':
                        if samples>=8192:continue
                        samples+=1
                    if row['type']=='episode':episodes.append(row)
                    dest.write(line)
    finally:
        for s in streams:s.close()
    if len(episodes)!=a.episodes*3 or any(not r.get('difficulty') or r.get('engineErrors') or r.get('actionErrors') for r in episodes):raise RuntimeError('Incomplete/invalid demo episodes')
    manifest['demonstrations']={'episodes':len(episodes),'samplesUsed':samples,'wins':sum(r['victory'] for r in episodes),'byDifficulty':{d:{'games':sum(r['difficulty']==d for r in episodes),'wins':sum(r['difficulty']==d and r['victory'] for r in episodes)} for d in ['NORMAL','HARD','ABYSS']},'combinedSha256':sha(combined)};save()
    rec=run('bc',['--seed','101','bc','--data',str(combined),'--epochs','6','--max-samples','8192','--out',str(out/'bc.pt')]);manifest['jobs'].append(rec);save()
    if rec['exitCode']:raise RuntimeError('BC failed')
    # Same initial BC policy and RNG seed: only discount differs. Gamma=1 avoids
    # discounted failure rewarding longer sequences of fruitless shop actions.
    configs=[]
    for label,gamma in [('ppo-g099',0.99),('ppo-g1',1.0)]:
        config=json.loads((lab/'rl/config.json').read_text());config['gamma']=gamma
        f=out/(label+'-config.json');f.write_text(json.dumps(config,indent=2)+'\n');configs.append((label,f))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,label,['--seed','20001','ppo','--checkpoint',str(out/'bc.pt'),'--out',str(out/(label+'.pt')),'--steps-per-stage',str(a.steps_per_stage),'--curriculum','NORMAL,HARD,ABYSS'],f) for label,f in configs]
        for future in as_completed(futures):
            rec=future.result();manifest['jobs'].append(rec);save();print(json.dumps(rec),flush=True)
    if any(r['exitCode'] for r in manifest['jobs']):raise RuntimeError('A training arm failed')
    manifest['checkpoints']={f.name:sha(f) for f in out.glob('*.pt')}
    if any(sha(Path(f))!=h for f,h in sources.items()) or sha(split)!=manifest['seedSplitSha256']:raise RuntimeError('Source/split changed during training')
    manifest['status']='complete'
except Exception as e:manifest['status']='failed';manifest['error']=repr(e);raise
finally:manifest['finishedAt']=stamp();save()
