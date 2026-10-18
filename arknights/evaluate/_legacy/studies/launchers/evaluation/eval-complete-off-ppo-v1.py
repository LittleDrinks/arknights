"""Audit completed OFF arm independently of failed paired run, then fixed128 frozen dev games."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import json
import sys
import time
root=Path('/data1/zsm/arknights');code=root/'code-wide-dev-v1'
sys.path.insert(0,str(code/'wide-dev'))
import contracts_wide as W
P,C,F=W.P,W.C,W.F
out=root/'experiments/ppo-fit-off-frozen-dev-v1';C.new_output(out)
train=root/'experiments/ppo-fit-timing-v1'
outer=json.loads((train/'manifest.json').read_bytes());side=json.loads((train/'off.pt.manifest.json').read_bytes())
assert outer['status']=='failed' and side['status']=='complete' and side['totalSteps']==32768 and side['lastUpdate']==128
assert side['outputSHA256']==C.sha(train/'off.pt')
assert side['checkpointInput']['sha256']=='af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b'
source=W.source_identity(code);game_path,game,pin=F.game_identity(root,code)
assert source['prep']==outer['source'] and (game_path,game)==P.game_identity(root)
assert game==outer['game']
inputs={str(train/'manifest.json'):C.sha(train/'manifest.json'),str(train/'off.pt.manifest.json'):C.sha(train/'off.pt.manifest.json'),
 str(train/'off.pt.jsonl'):C.sha(train/'off.pt.jsonl'),str(train/'off.pt'):C.sha(train/'off.pt'),**outer['sourceSHA256'],**side['sourceSHA256']}
updates=[json.loads(x) for x in (train/'off.pt.jsonl').open()]
assert len(updates)==128 and [x['totalSteps'] for x in updates]==list(range(256,32769,256))
assert sum(x['completedEpisodes'] for x in updates)==side['completedEpisodes']==545
files=sorted((train/'off-episodes').glob('*.json'));assert len(files)==545
wins=[]
for p in files:
 assert not p.is_symlink()
 episode=json.loads(p.read_bytes());t=episode['terminal'];inputs[str(p)]=C.sha(p)
 assert episode['controller']==t['controller']==P.VERSIONS['off'] and len(episode['actions'])==t['steps']
 own=C.terminal_contract(t,seed=t['episode']['seed'],steps=t['steps'],schema=t['schema'])
 assert t['episode']['partners']['split']=='train'
 if t['victory']:wins.append(dict(seed=t['episode']['seed'],individual=own['victory'],sha256=C.sha(p)))
assert all(C.sha(Path(p))==h for p,h in inputs.items())
rows=[dict(id=f'{m}-{v}-{p}-{s:02}',model=m,controller=v,pool=p,seed=s,mode='coop',difficulty='ABYSS',seat=0,
 status='unrun',steps=0,eof=False,error=None,teamVictory=None,learnerIndividualVictory=None)
 for s in range(1,17) for v in P.VERSIONS for p in C.TEAMS for m in ('warmBC','offPPO')]
P.write_new(out/'manifest.json',dict(kind='complete-off-only-PPO-frozen-dev/v1',games=rows,denominator=128,
 pairedRunStatus='failed; deferred incomplete not evaluated',trainingCompleteSteps=32768,trainingCompleteEpisodes=545,
 trainingWinsChangingPolicy=wins,source=source,game=game,inputSHA256=inputs,validationUsed=False,scriptSHA256=C.sha(Path(__file__))))
seal_manifest=P.seal_manifest(out)
L=C.learner(code);models={}
for m,p in [('warmBC',root/'experiments/bc-fit-budget-train-v1/steps3000.pt'),('offPPO',train/'off.pt')]:
 model,ckpt=L.load_checkpoint(p);model.eval().requires_grad_(False);models[m]=(model,ckpt)
parameters={m:L.digest(x[0]) for m,x in models.items()}
seals={};started=time.monotonic();deadline=started+900
with ThreadPoolExecutor(max_workers=8) as executor:
 pending={executor.submit(P.game_job,r,*models[r['model']],game_path,out,min(deadline-60,time.monotonic()+120),64*F.MIB,F.G.pool_sha(code)):i for i,r in enumerate(rows)}
 for future in as_completed(pending):
  i=pending[future];rows[i],seals[rows[i]['id']]=future.result()
assert all(C.sha(Path(p))==h for p,h in inputs.items())
assert W.source_identity(code)==source and F.game_identity(root,code)==(game_path,game,pin)
assert {m:L.digest(x[0]) for m,x in models.items()}==parameters
P.audit_evidence(out,rows,seals,seal_manifest,64*F.MIB,deadline)
assert all(r['status']=='complete' for r in rows)
cells={}
for m in models:
 for v in P.VERSIONS:
  for p in C.TEAMS:
   selected=[r for r in rows if (r['model'],r['controller'],r['pool'])==(m,v,p)]
   cells[f'{m}/{v}/{p}']=dict(denominator=len(selected),complete=len(selected),teamWins=sum(r['teamVictory'] is True for r in selected),
    individualWins=sum(r['learnerIndividualVictory'] is True for r in selected))
P.write_new(out/'summary.json',dict(status='complete',integrity='verified',games=rows,evidence=seals,cells=cells,
 source=source,parametersUnchanged=True,trainingWinsChangingPolicy=wins,seconds=time.monotonic()-started,validationUsed=False))
print(json.dumps(cells))
