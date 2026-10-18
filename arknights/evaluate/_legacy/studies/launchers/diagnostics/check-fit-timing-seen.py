"""Seen training-seed diagnostic: perfect-label-fit policies x prep timing, never validation."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

os.sched_setaffinity(0, {12,13,14,15})
root = Path('/data1/zsm/arknights')
code = root / 'experiment-code'
sys.path.insert(0, str(code / 'bc-fit-budget'))
import contracts_fit as F
spec = importlib.util.spec_from_file_location('timing_seen_helpers', code / 'prep-timing/run.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
C = F.C
out = root / 'experiments/bc-fit-timing-seen-v1'
C.new_output(out)
training = root / 'experiments/bc-fit-budget-train-v1'
report = json.loads((training / 'train.json').read_bytes())
train_sha = C.sha(training / 'train.json')
assert report['status']=='complete' and report['creditGranted'] is True
assert report['positiveGames']==6 and report['positiveLabels']==498
metadata = json.loads((training / 'input-metadata.json').read_bytes())
paths = json.loads((training / 'admission-paths.json').read_bytes())
cases = [r for r in json.loads((root / 'experiments/natural-coop-teacher-search-v1/manifest.json').read_bytes())['games']
         if r.get('eligiblePositive')]
assert len(cases)==6 and {r['seed'] for r in cases}==set(report['episodeGroups'])
source, fit_source = P.source_identity(), F.source_identity(code)
assert report['source']==fit_source
game_path, game = P.game_identity(root)
assert report['game']==game
parent = F.check_parent(root)
L = C.learner(code)
models = {}
hashes = {}
for name, count in [('steps1000',1000),('steps3000',3000)]:
    file = training / (name+'.pt')
    record = report['models'][name]
    assert record['actualUpdates']==count and record['actualDraws']==count*34
    assert record['after']['accuracy']==1.0
    hashes[name] = C.sha(file)
    assert hashes[name]==record['checkpointSHA256']
    model, ckpt = L.load_checkpoint(file)
    F.model_guard(L, model, ckpt, game)
    assert ckpt['metadata']['metrics']=={k:v for k,v in record.items() if k!='checkpointSHA256'}
    assert L.digest(model)==record['parameterSHA256']
    model.eval()
    model.requires_grad_(False)
    models[name]=(model,ckpt)

def guard():
    assert P.source_identity()==source and F.source_identity(code)==fit_source
    assert P.game_identity(root)==(game_path,game) and C.sha(parent)==F.PARENT_SHA
    assert C.sha(training / 'train.json')==train_sha
    assert {p.name for p in training.iterdir()}==set(report['evidence'])|{'train.json'}
    for name, identity in report['evidence'].items():
        assert F.identity(training / name)==identity
    F.recheck_inputs(metadata,paths)
    for name,(model,_) in models.items():
        assert C.sha(training / (name+'.pt'))==hashes[name]
        assert L.digest(model)==report['models'][name]['parameterSHA256']
guard()
rows=[dict(id=f'{name}-{mode}-{r["pool"]}-{r["seed"]}',model=name,controller=mode,
           pool='original' if r['pool']=='normal' else 'mixed',seed=r['seed'],mode='coop',difficulty='ABYSS',
           seat=0,status='unrun',steps=0,eof=False,error=None,teamVictory=None,learnerIndividualVictory=None)
      for name in models for mode in P.VERSIONS for r in cases]
assert len(rows)==24
P.write_new(out / 'manifest.json',dict(kind='seen teacher-success seeds, diagnostic only',planned=24,
    models=hashes,trainSHA256=train_sha,games=rows,source=source,fitSource=fit_source,
    trainingGroups=report['episodeGroups'],validationUsed=False,cpuAffinity=sorted(os.sched_getaffinity(0)),
    limits=dict(seconds=600,workers=4,perGameSeconds=120,perGameMiB=64)))
manifest_identity=P.seal_manifest(out)
seals={}
started=time.monotonic()
deadline=started+600
error=None
try:
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending={}
        index=0
        failed=False
        while pending or index<len(rows):
            while not failed and len(pending)<4 and index<len(rows) and time.monotonic()<deadline:
                row=rows[index]
                future=executor.submit(P.game_job,row,*models[row['model']],game_path,out,
                    min(deadline,time.monotonic()+120),64*F.MIB,F.G.pool_sha(code))
                pending[future]=index
                index+=1
            if not pending:
                break
            done,_=wait(pending,timeout=.25,return_when=FIRST_COMPLETED)
            for future in done:
                i=pending.pop(future)
                try:
                    rows[i],seals[rows[i]['id']]=future.result()
                except Exception as exc:
                    rows[i].update(status='failed',error=repr(exc),evidenceUnsealed=True)
                    error=repr(exc)
                    failed=True
    assert error is None,error
    guard()
    P.audit_evidence(out,rows,seals,manifest_identity,64*F.MIB,deadline)
    assert sum(p.stat().st_size for d in out.iterdir() if d.is_dir() for p in d.iterdir())<=24*64*F.MIB
except BaseException as exc:
    error=repr(exc)
    for row in rows:
        if row['status']=='complete':
            row.update(status='failed',error='Run invalidated: '+error,teamVictory=None,learnerIndividualVictory=None)
summary=dict(status='complete' if error is None and all(r['status']=='complete' for r in rows) else 'failed',
    error=error,planned=24,complete=sum(r['status']=='complete' for r in rows),
    failed=sum(r['status']=='failed' for r in rows),unrun=sum(r['status']=='unrun' for r in rows),
    teamWins=sum(r['status']=='complete' and r['teamVictory'] is True for r in rows),
    individualWins=sum(r['status']=='complete' and r['learnerIndividualVictory'] is True for r in rows),
    games=rows,evidence=seals,manifest=manifest_identity,seconds=time.monotonic()-started)
P.write_new(out / 'summary.json',summary)
print(json.dumps({k:v for k,v in summary.items() if k not in ('games','evidence')}))
raise SystemExit(0 if summary['status']=='complete' else 1)
