"""Two actual known-positive teacher cases through the exact durable scale collector APIs."""
import gzip
import json
import os
from pathlib import Path
import sys
import threading
import time

root=Path('/data1/zsm/arknights')
code=root/'experiment-code'
sys.path.insert(0,str(code/'teacher-scale'))
from reuse import C,N,source_identity,game_identity
from storage import Budget,new_output,publish
from collector import run_lanes
from reconcile import reconcile

out=root/'experiments/teacher-scale-real-preflight-v1'
new_output(out)
for name in ('journals','cases','lanes'):(out/name).mkdir()
rows=[dict(index=i,seed=seed,pool=pool,lane=pool+'-00',status='not-run')
      for i,(seed,pool) in enumerate([(30072,'normal'),(30122,'mixed')])]
budget=Budget()
source=source_identity()
game_path,game=game_identity(root)
pool=N.pool_sha(code)
before=dict(source=source,game=game,poolSHA256=pool)
publish(out/'plan.json',dict(games=rows,planned=2,split='train'),budget)
publish(out/'source-before.json',before,budget)
manifest=dict(status='running',kind='two-case real collector preflight, not the 8192 production batch',
              planned=2,games=rows,lanes={},globalErrors=[])
publish(out/'manifest.json',manifest,budget,snapshot=True)
commands={}
for kind,team in [('normal','original-v1,original-v1,original-v1'),('mixed','original-v1,buy2-v1,saver-v1')]:
    env={k:v for k,v in C.clean_env().items() if not k.startswith(('ARK_','COOP_'))}
    env.update(COOP_POOL=kind,COOP_SPLIT='train',COOP_POOL_SEED='313',COOP_TEAM=team,UV_THREADPOOL_SIZE='1')
    commands[kind]=(['node','--max-old-space-size=768',str(code/'coop/env.mjs'),'--game',str(game_path)],env)
start=time.monotonic()
run_lanes(out,rows,commands,game,pool,budget,manifest,start+120,start+180,threading.Event())
assert source_identity()==source and game_identity(root)==(game_path,game)
publish(out/'source-after.json',before,budget)
audit=reconcile(out,[dict(index=i,seed=seed,pool=p,lane=p+'-00',status='not-run')
                     for i,(seed,p) in enumerate([(30072,'normal'),(30122,'mixed')])],time.monotonic()+120)
assert not audit['errors'],audit['errors']
assert not manifest['globalErrors'],manifest['globalErrors']
comparisons=[]
for row in audit['games']:
    assert row['status']=='complete' and row['eligiblePositive'] is True
    raw=json.loads(gzip.decompress((out/row['artifact']).read_bytes()))
    original=root/f'experiments/natural-coop-teacher-search-v1/{row["pool"]}-{row["seed"]}.json.gz'
    expected=json.loads(gzip.decompress(original.read_bytes()))
    assert raw==expected,(row['seed'],'full demo differs')
    comparisons.append(dict(seed=row['seed'],pool=row['pool'],entireOriginalDemoExact=True,
                            samples=len(raw['samples']),rawSHA256=row['artifactIdentity']['payloadSHA256']))
assert all(l['closeACKAndProcessEOF'] is True for l in audit['lanes'].values())
manifest.update(status='complete',games=audit['games'],lanes=audit['lanes'],files=audit['files'],
                filesSHA256=audit['filesSHA256'],frozen=True,trainingAllowed=False,comparisons=comparisons,
                seconds=time.monotonic()-start)
publish(out/'manifest.json',manifest,budget,snapshot=True)
print(json.dumps(dict(status='complete',comparisons=comparisons,lanesEOF=True,seconds=manifest['seconds'])))
