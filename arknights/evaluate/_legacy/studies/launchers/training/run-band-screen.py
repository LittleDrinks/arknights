"""Screen legal solo opening choices, without changing the rule engine or shop data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import os
from pathlib import Path
import subprocess
import time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--game',type=Path,required=True)
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--seeds',type=int,default=8)
parser.add_argument('--parallel-bands',type=int,default=3)
args=parser.parse_args()
game=args.game.resolve(); out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
bands=json.loads((game/'data/bands.json').read_text())
entries=[(k,v.get('name',k)) for k,v in bands.items() if v.get('visible',True)]
manifest={'kind':'solo band-conditioned upstream bot screening; not a trained model',
          'rules_commit':'6ab2468279e926d107788f63d7fe219b85f67b4a',
          'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'seed_start':1,'seeds_per_band':args.seeds,'rehearsal':0,'jobs_per_band':4,
          'parallel_bands':args.parallel_bands,'status':'running','arms':[],
          'limits':'Development seeds only. Changed legal selected band through original _applyBand; no altered band mechanics, shop offers or enemies. Fixed-band success is conditional and requires later legal choice policy plus heldout evaluation.'}
def save(): (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
save()
def one(item):
    band,name=item; env=dict(os.environ);env['ARK_GAME_ROOT']=str(game)
    cmd=['node',str(Path(__file__).resolve().with_name('clean-bandbench.mjs')),
         '--configs','solo:ABYSS','--seed','1','--seeds',str(args.seeds),'--jobs','4',
         '--band',band,'--rehearsal','0','--quiet','--json',str(out/(band+'.json'))]
    start=time.monotonic()
    with (out/(band+'.log')).open('w') as log:
        result=subprocess.run(cmd,env=env,cwd=game,stdout=log,stderr=subprocess.STDOUT)
    rec={'band':band,'name':name,'exit_code':result.returncode,'batch_wall_seconds':time.monotonic()-start}
    if result.returncode==0:
        rows=json.loads((out/(band+'.json')).read_text());rec.update(games=len(rows),wins=sum(r['victory'] for r in rows),
            engine_errors=sum(r['errors'] for r in rows),mean_rounds=sum(r['roundsPassed'] for r in rows)/len(rows))
    return rec
with ThreadPoolExecutor(max_workers=args.parallel_bands) as pool:
    for rec in pool.map(one,entries):
        manifest['arms'].append(rec);save();print(json.dumps(rec,ensure_ascii=False),flush=True)
manifest['status']='complete' if all(r['exit_code']==0 for r in manifest['arms']) else 'complete_with_failed_arms'
manifest['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
