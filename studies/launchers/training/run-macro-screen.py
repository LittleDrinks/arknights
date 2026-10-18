"""Paired 2^3 stacking-module ablation, using genuine unmodified engine battles."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--kind',choices=['macro','teacher'],default='macro')
p.add_argument('--root',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--seeds',type=int,default=16)
p.add_argument('--seed',type=int,default=1)
p.add_argument('--jobs',type=int,default=4)
p.add_argument('--parallel',type=int,default=3)
a=p.parse_args()
if min(a.seeds,a.seed,a.jobs,a.parallel)<1:raise SystemExit('positive integer arguments required')
root=a.root.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
game=root/'game';lab=root/'experiment-code';entry=root/'tools/clean-bandbench.mjs'
if (out/'screen-manifest.json').exists():raise SystemExit('Use a new output directory')
expected=json.loads((lab/'policy/pins/rules-manifest.json').read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rules():
    files={str(f.relative_to(game)):sha(f) for d in ['server','data','shared'] for f in (game/d).rglob('*') if f.is_file() and f.suffix in ['.js','.mjs','.json']}
    files.update({x:sha(game/x) for x in ['package.json','package-lock.json']})
    if files!=expected['files']:raise RuntimeError('Pinned rule set/hash mismatch')
    return expected['treeSha256']
source_files=[entry,*sorted((lab/'macros').glob('*.mjs'))]
if a.kind=='teacher':source_files+=sorted((lab/'teacher').glob('*.mjs'))
hashes={str(f):sha(f) for f in source_files}
names=['refresh','acquire','lappland']
arms=[','.join(n for n,b in zip(names,bits) if b) or 'off' for bits in itertools.product([0,1],repeat=3)]
if a.kind=='teacher':arms=['off:conservative',*[v+':'+b for v in ['refresh','acquire','mixed'] for b in ['conservative','aggressive']]]
configs=['solo:ABYSS','coop4:ABYSS']
stamp=lambda:datetime.now(timezone.utc).isoformat()
manifest={'experiment':'stacking module factorial','trainedModel':False,'macroCommit':'0245a1e558766785db1c9502412f01ab2878eb89','rulesCommit':expected['rulesCommit'],'ruleTreeSha256':rules(),'sourceSha256':hashes,'plannedGames':len(arms)*2*a.seeds,'seedStart':a.seed,'seedsPerMode':a.seeds,'configs':configs,'options':{'maxBuyPrice':3},'status':'running','startedAt':stamp(),'arms':[]}
if a.kind=='teacher':manifest.update(experiment='knowledge teacher variants',macroCommit='0245a1e558766785db1c9502412f01ab2878eb89',teacherCommit='3628c86c9ab92852711d4063eb97ed0391792912')
def save():(out/'screen-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
save()
def run(arm):
    label=arm.replace(',','+').replace(':','-');dest=out/(label+'.json')
    if dest.exists():raise RuntimeError('Existing arm output')
    env={**os.environ,'ARK_GAME_ROOT':str(game),'ARK_STACKING_GAME_ROOT':str(game),'ARK_STACKING':arm,'ARK_STACKING_TAIL':'1','ARK_STACKING_OPTIONS':json.dumps(manifest['options']),'ARK_STACKING_RUNTIME':str(lab/'macros/stacking-runtime.mjs')}
    loader=lab/'macros/stacking-loader.mjs'
    if a.kind=='teacher':
        variant,budget=arm.split(':');env.pop('ARK_STACKING_RUNTIME',None);env['ARK_STACKING']='off'
        env.update(ARK_TEACHER=variant,ARK_TEACHER_BUDGET=budget,ARK_TEACHER_GAME_ROOT=str(game),ARK_TEACHER_RUNTIME=str(lab/'teacher/policy.mjs'))
        loader=lab/'teacher/loader.mjs'
    cmd=['node','--loader',str(loader),str(entry),'--configs',','.join(configs),'--seeds',str(a.seeds),'--seed',str(a.seed),'--jobs',str(a.jobs),'--rehearsal','0','--json',str(dest),'--quiet']
    record={'arm':arm,'command':cmd,'startedAt':stamp(),'status':'failed'}
    try:
        with (out/(label+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=game,env=env,stdout=log,stderr=subprocess.STDOUT)
        record['exitCode']=r.returncode
        if r.returncode:raise RuntimeError('benchmark process failed')
        rows=json.loads(dest.read_text())
        planned={(c,s) for c in configs for s in range(a.seed,a.seed+a.seeds)}
        keys=[(r['config'],r['seed']) for r in rows]
        if len(keys)!=len(planned) or set(keys)!=planned:raise RuntimeError('Incomplete/duplicate/unexpected games')
        if any(type(r['victory']) is not bool or type(r['errors']) is not int or r['errors']<0 for r in rows):raise RuntimeError('Invalid outcome')
        record['games']=len(rows);record['wins']=sum(r['victory'] and r['errors']==0 for r in rows)
        record['engineErrors']=sum(r['errors'] for r in rows)
        if a.kind=='teacher':
            for row in rows:row['stacking']={'accepted':row['teacher']['macroAccepted'],'calls':row['teacher']['calls'],'netCost':row['teacher']['macroNetCost']}
            record['setupBuys']=sum(row['teacher']['setupBuys'] for row in rows)
            record['setupGrossCost']=sum(row['teacher']['setupGrossCost'] for row in rows)
        record['macroAccepted']=sum(r['stacking']['accepted'] for r in rows)
        record['macroActiveGames']=sum(r['stacking']['accepted']>0 for r in rows)
        record['macroCalls']=sum(r['stacking']['calls'] for r in rows)
        record['macroNetCost']=sum(r['stacking']['netCost'] for r in rows)
        if record['engineErrors']:raise RuntimeError('Engine errors')
        record['status']='complete'
    except Exception as e:record['error']=repr(e)
    record['finishedAt']=stamp();return record
try:
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        for future in as_completed([pool.submit(run,arm) for arm in arms]):
            record=future.result();manifest['arms'].append(record);save();print(json.dumps(record),flush=True)
    manifest['ruleTreeAfter']=rules()
    if any(sha(Path(f))!=h for f,h in hashes.items()):raise RuntimeError('Experiment source changed during run')
    manifest['status']='complete' if all(r['status']=='complete' for r in manifest['arms']) else 'failed'
except Exception as e:manifest['status']='failed';manifest['error']=repr(e)
finally:manifest['finishedAt']=stamp();save()
if manifest['status']!='complete':raise SystemExit(1)
