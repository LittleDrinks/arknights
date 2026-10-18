"""One-time prospective 32-seed full factorial; every outcome retained, no training."""
from pathlib import Path
import json
import os
import signal
import sys
import time
from collections import defaultdict
root=Path('/data1/zsm/arknights');code=root/'code-wide-dev-v1'
sys.path.insert(0,str(code/'wide-dev'))
import contracts_wide as W
import admission_wide as A
import evaluation as V
P,C,F=W.P,W.C,W.F

def main():
 started=time.monotonic();deadline=started+1500
 def alarm(*_):raise TimeoutError('Finite prospective validation 1500s budget')
 signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,1500)
 out=root/'experiments/frozen-wide-validation-v1';C.new_output(out)
 prior=root/'experiments/wide-dev-completion-v1/summary.json';d=json.loads(prior.read_bytes())
 assert d['status']=='complete' and d['integrity']=='verified' and d['complete']==2048 and d['creditGranted']
 rows=[dict(id=f'{m}-{v}-{p}-{s}',model=m,controller=v,pool=p,seed=s,episodeGroup=s,mode='coop',difficulty='ABYSS',seat=0,
  status='unrun',steps=0,eof=False,error=None,teamVictory=None,learnerIndividualVictory=None)
  for s in range(90017,90049) for v in P.VERSIONS for p in C.TEAMS for m in W.MODELS]
 assert len(rows)==256
 controls={'manifest.json':W.write_new(out/'manifest.json',dict(kind='prospective-frozen-ABYSS-validation/v1',
  games=rows,denominator=256,independentSeedGroups=32,validationSeeds=list(range(90017,90049)),split='eval',trainingAllowed=False,
  parentSHA256=F.PARENT_SHA,candidateSHA256='af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b',
  candidateChosenBeforeWideEvaluation=True,candidateController='deferred',primaryComparison='candidate/deferred vs parent/off, within each pool',
  secondaryComparison='same-controller model comparison and candidate/off diagnostic',
  selectionReason='Same-controller development 4/512 vs 0/512; best existing parent/off also 4/512, no superiority claim',
  priorDevelopmentSHA256=C.sha(prior),scriptSHA256=C.sha(Path(__file__)),
  exposure='90017..90048 first opening; 90001..90016 previously exposed;100001..100064 remain sealed'),4*F.MIB)}
 exposure=root/'experiments/seed-exposure-validation-v2.json'
 P.write_new(exposure,dict(kind='seed-exposure/v1',seeds=list(range(90017,90049)),firstRun=str(out),
  status='exposed-at-launch',purpose='one-time frozen complete256-case validation',manifestSHA256=C.sha(out/'manifest.json')))
 source=W.source_identity(code);game_path,game,pin=F.game_identity(root,code);L=C.learner(code)
 models,admitted,check=A.admit(L,root/'experiments/bc-fit-budget-train-v1',F.check_parent(root),
  'af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b',
  '8a7e6c600066c8b6fb7c8606375f217961514141396d3b24260cea3df073e2ff',source['fit'],game,pin)
 controls['admission.json']=W.write_new(out/'admission.json',dict(source=source,game=game,admission=admitted),8*F.MIB)
 config=dict(affinity=sorted(os.sched_getaffinity(0)),source=source,game=game,gamePath=str(game_path),out=str(out),
  deadline=deadline,models=admitted['models'],paths={'parent':str(F.check_parent(root)),'steps3000':str(root/'experiments/bc-fit-budget-train-v1/steps3000.pt')},
  files={'parent':W.digest(F.check_parent(root)),'steps3000':admitted['candidate']})
 seals,errors=V.dispatch(rows,models,game_path,out,deadline-120,16,120,64*F.MIB,F.G.pool_sha(code),worker_config=config)
 V.audit(out,rows,seals,controls,64*F.MIB,deadline);check(deadline)
 assert W.source_identity(code)==source and F.game_identity(root,code)==(game_path,game,pin)
 assert not errors and all(r['status']=='complete' for r in rows),errors
 cells={}
 for m in W.MODELS:
  for v in P.VERSIONS:
   for p in C.TEAMS:
    selected=[r for r in rows if (r['model'],r['controller'],r['pool'])==(m,v,p)]
    cells[f'{m}/{v}/{p}']=dict(denominator=len(selected),complete=len(selected),teamWins=sum(r['teamVictory'] is True for r in selected),
     individualWins=sum(r['learnerIndividualVictory'] is True for r in selected))
 result=dict(status='complete',integrity='verified',kind='prospective-frozen-ABYSS-validation/v1',complete=256,denominator=256,
  seedGroups=32,cells=cells,games=rows,evidence=seals,controls=controls,source=source,seconds=time.monotonic()-started,
  trainingAllowed=False,heldoutSeedsNowExposed=list(range(90017,90049)),sealedSeedsUntouched='100001..100064')
 W.write_new(out/'summary.json',result,16*F.MIB);signal.setitimer(signal.ITIMER_REAL,0)
 print(json.dumps({k:result[k] for k in ('status','complete','cells','seconds')}))
if __name__=='__main__':main()
