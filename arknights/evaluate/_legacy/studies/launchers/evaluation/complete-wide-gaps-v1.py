"""Complete ALL and only missing fixed wide-development cases; retain failed batch unchanged."""
from pathlib import Path
import json
import os
import signal
import sys
import time
root=Path('/data1/zsm/arknights');code=root/'code-wide-dev-v1'
sys.path.insert(0,str(code/'wide-dev'))
import contracts_wide as W
import admission_wide as A
import evaluation as V
P,C,F=W.P,W.C,W.F

def main():
 started=time.monotonic();deadline=started+1200
 def alarm(*_):raise TimeoutError('Finite 1200s completion/audit budget')
 signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,1200)
 old=root/'experiments/wide-dev-v1';out=root/'experiments/wide-dev-completion-v1';C.new_output(out)
 old_sha=C.sha(old/'summary.json');prior=json.loads((old/'summary.json').read_bytes())
 assert prior['integrity']=='verified' and all(prior['finalChecks'].values()) and prior['creditGranted'] is False
 plan=V.schedule();assert [x['id'] for x in plan]==[x['id'] for x in prior['games']]
 missing={r['id'] for r in prior['games'] if r['status']!='complete'}
 rows=[dict(r) for r in plan if r['id'] in missing];assert len(rows)==86
 source=W.source_identity(code);game_path,game,pin=F.game_identity(root,code)
 L=C.learner(code)
 models,admitted,check=A.admit(L,root/'experiments/bc-fit-budget-train-v1',F.check_parent(root),
  'af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b',
  '8a7e6c600066c8b6fb7c8606375f217961514141396d3b24260cea3df073e2ff',source['fit'],game,pin)
 controls={'manifest.json':W.write_new(out/'manifest.json',dict(kind='fixed-wide-development-missing-case-completion/v1',
  plannedFullDenominator=2048,missingCaseDenominator=len(rows),oldSummarySHA256=old_sha,source=source,
  game=game,admission=admitted,games=rows,selection='ALL previously noncomplete rows; no win-based selection',
  scriptSHA256=C.sha(Path(__file__)),validationUsed=False),8*F.MIB)}
 config=dict(affinity=sorted(os.sched_getaffinity(0)),source=source,game=game,gamePath=str(game_path),out=str(out),
  deadline=deadline,models=admitted['models'],paths={'parent':str(F.check_parent(root)),'steps3000':str(root/'experiments/bc-fit-budget-train-v1/steps3000.pt')},
  files={'parent':W.digest(F.check_parent(root)),'steps3000':admitted['candidate']})
 seals,errors=V.dispatch(rows,models,game_path,out,deadline-180,16,120,64*F.MIB,F.G.pool_sha(code),worker_config=config)
 V.audit(out,rows,seals,controls,64*F.MIB,deadline)
 assert not errors,errors
 assert all(r['status']=='complete' for r in rows)
 # Independent payload/result/model-proof audit of every original executed case, including failures.
 for row in prior['games']:
  if row['id'] in prior['evidence']:V.verify_job(row,old,prior['evidence'][row['id']],64*F.MIB,deadline)
 replacement={r['id']:r for r in rows};merged=[replacement.get(r['id'],r) for r in prior['games']]
 assert len(merged)==2048 and all(r['status']=='complete' for r in merged)
 check(deadline)
 assert C.sha(old/'summary.json')==old_sha and W.source_identity(code)==source
 assert F.game_identity(root,code)==(game_path,game,pin)
 result=dict(kind='fixed-wide-development-completion/v1',status='complete',integrity='verified',creditGranted=True,
  oldSummarySHA256=old_sha,originalComplete=1962,completionCases=rows,evidence=seals,controls=controls,
  source=source,allOriginalExecutedCasesAudited=1978,validationUsed=False,games=merged,**V.summary(merged),seconds=time.monotonic()-started)
 W.write_new(out/'summary.json',result,24*F.MIB)
 signal.setitimer(signal.ITIMER_REAL,0)
 print(json.dumps({k:result[k] for k in ('status','complete','teamWins','individualWins','cells','paired','seconds')}))
if __name__=='__main__':main()
