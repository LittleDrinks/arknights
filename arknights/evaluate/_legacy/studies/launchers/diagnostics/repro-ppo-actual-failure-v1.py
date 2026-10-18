"""Actual captured failed rollout -> original guard. No optimizer step, no threshold change."""
import importlib.util,json,sys,time
from pathlib import Path
root=Path('/data1/zsm/arknights');code=root/'code-wide-dev-v1'
sys.path.insert(0,str(code))
from rl import learner as L
spec=importlib.util.spec_from_file_location('original_parallel_train',code/'parallel-rl/train.py')
T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)
p=root/'experiments/ppo-numeric-capture-v1/deferred.pt.numeric-failure.pt'
data=L.torch.load(p,map_location='cpu',weights_only=False)
model=L.ActorCritic(*data['dimensions']);model.load_state_dict(data['model']);model.eval()
assert L.digest(model)==data['behaviorSHA256']
original=L.digest(model)
class GuardPassed(Exception):pass
optimizer=L.torch.optim.SGD(model.parameters(),lr=0.0)
def no_step():raise GuardPassed()
optimizer.step=no_step

def check(results):
 try:
  T.update(model,optimizer,results,data['config'])
  raise AssertionError('Unexpected optimizer completion')
 except GuardPassed:return dict(red=False,error=None)
 except RuntimeError as exc:
  if 'Behavior policy mismatch:' not in str(exc):raise
  return dict(red=True,error=str(exc))

def sub(trajectory,indices):
 t={k:[trajectory[k][i] for i in indices] for k in T.FIELDS}
 t['ends'][-1]=True
 return [dict(worker=0,trajectory=t)]

started=time.monotonic()
full=check(data['results']);assert full['red']
merged,_,_=T.merge_trajectories(data['results'],data['config'])
chunks=[];witness=None
for start in range(0,len(merged['actions']),32):
 r=sub(merged,list(range(start,min(start+32,len(merged['actions'])))))
 verdict=check(r);chunks.append(dict(start=start,states=len(r[0]['trajectory']['actions']),**verdict))
 if verdict['red'] and witness is None:witness=r
assert witness
# Greedy one-state removal: every retained state is necessary under this exact ordering.
changed=True
while changed and len(witness[0]['trajectory']['actions'])>1:
 changed=False;t=witness[0]['trajectory'];n=len(t['actions'])
 for i in range(n):
  smaller=sub(t,[j for j in range(n) if j!=i])
  if check(smaller)['red']:witness=smaller;changed=True;break
verdict=check(witness);assert verdict['red'] and L.digest(model)==original
wpath=p.with_name('minimal-sync-witness-v1.pt')
assert not wpath.exists()
L.torch.save(dict(kind='minimal-actual-ppo-sync-witness/v1',model=data['model'],dimensions=data['dimensions'],
 results=witness,config=data['config'],behaviorSHA256=data['behaviorSHA256'],originalCapture=str(p)),wpath)
report=dict(kind='actual-ppo-sync-failure-reproduction/v1',actualSampledFailedRollout=True,full=full,chunks=chunks,
 minimalStates=len(witness[0]['trajectory']['actions']),minimal=verdict,unchangedModelParameters=True,
 originalFailureUpdate=data['behaviorVersion']+1,priorCompletedSteps=data['totalCompletedSteps'],
 witness=str(wpath),seconds=time.monotonic()-started)
(root/'experiments/ppo-numeric-capture-v1/reproduction.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report));sys.exit(42)
