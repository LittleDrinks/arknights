"""Batch fixed profiles on paired development seeds; keep every per-arm raw output."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
from pathlib import Path
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--game',type=Path,required=True)
p.add_argument('--lab',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--entry',type=Path,required=True)
p.add_argument('--seeds',type=int,default=16)
p.add_argument('--seed',type=int,default=1)
p.add_argument('--jobs',type=int,default=4)
p.add_argument('--parallel',type=int,default=3)
p.add_argument('--rehearsal',type=int,default=0)
p.add_argument('--code-revision',required=True)
p.add_argument('--profiles',nargs='*')
a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
profiles=sorted((a.lab/'policy/profiles').glob('*.json'))
if not profiles:raise SystemExit('No profiles found; abort without running an empty experiment')
if a.profiles is not None:
    profiles=[f for f in profiles if f.stem in a.profiles]
    if {f.stem for f in profiles}!=set(a.profiles):raise SystemExit('Unknown profile requested')
manifest={'experiment':'fixed policy profile screening','codeRevision':a.code_revision,
          'plannedGames':len(profiles)*a.seeds*2,'seedStart':a.seed,'seedCountPerMode':a.seeds,
          'configs':['solo:ABYSS','coop4:ABYSS'],'jobsPerArm':a.jobs,'parallelArms':a.parallel,
          'rehearsal':a.rehearsal,'status':'running','startedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'trainedModel':False,'arms':[]}
def save():(out/'screen-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
save()
def run(profile):
    cmd=['node',str(a.lab.resolve()/'policy/run.mjs'),'--game',str(a.game.resolve()),
         '--entry',str(a.entry.resolve()),'--profile',str(profile.resolve()),'--out',str(out/(profile.stem+'.json')),
         '--configs',','.join(manifest['configs']),'--seeds',str(a.seeds),'--seed',str(a.seed),
         '--jobs',str(a.jobs),'--rehearsal',str(a.rehearsal),'--quiet']
    with (out/(profile.stem+'.driver.log')).open('w') as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
    record={'profile':profile.stem,'exitCode':r.returncode,'command':cmd}
    f=out/(profile.stem+'.json')
    if f.exists():
        rows=json.loads(f.read_text());record['completedGames']=len(rows);record['results']={}
        for mode in manifest['configs']:
            group=[row for row in rows if row['config']==mode]
            record['results'][mode]={'games':len(group),'wins':sum(row['victory'] for row in group),
                                    'engineErrors':sum(row['errors'] for row in group),
                                    'meanRoundsPassed':sum(row['roundsPassed'] for row in group)/len(group) if group else None}
    return record
with ThreadPoolExecutor(max_workers=a.parallel) as pool:
    for record in pool.map(run,profiles):
        manifest['arms'].append(record);save();print(json.dumps(record,ensure_ascii=False),flush=True)
manifest['status']='complete' if all(row['exitCode']==0 for row in manifest['arms']) else 'complete_with_failed_arms'
manifest['finishedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
