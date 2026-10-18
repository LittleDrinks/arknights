"""Reusable two-state red loop and one-variable numerical probes; no file writes/updates."""
import importlib.util,json,sys,hashlib
from pathlib import Path
root=Path('/data1/zsm/arknights');code=root/'code-wide-dev-v1';sys.path.insert(0,str(code))
from rl import learner as L
spec=importlib.util.spec_from_file_location('original_parallel_train',code/'parallel-rl/train.py')
T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)
p=root/'experiments/ppo-numeric-capture-v1/minimal-sync-witness-v1.pt'
inputSHA=hashlib.sha256(p.read_bytes()).hexdigest()
d=L.torch.load(p,map_location='cpu',weights_only=False);m=L.ActorCritic(*d['dimensions']);m.load_state_dict(d['model']);m.eval()
before=L.digest(m);assert before==d['behaviorSHA256']
rollout,_,_=T.merge_trajectories(d['results'],d['config']);features=rollout['features'];actions=L.torch.tensor(rollout['actions']);old=L.torch.tensor(rollout['logprobs'])
class GuardPassed(Exception):pass
optimizer=L.torch.optim.SGD(m.parameters(),lr=0.0)
def no_step():raise GuardPassed()
optimizer.step=no_step
try:T.update(m,optimizer,d['results'],d['config']);raise AssertionError('Unexpected optimizer completion')
except GuardPassed:verdict='guard-passed'
except RuntimeError as error:
 if 'Behavior policy mismatch:' not in str(error):raise
 verdict=str(error)
with L.torch.no_grad():
 batch=m(features)[0].log_prob(actions)
 single=L.torch.tensor([m([f])[0].log_prob(L.torch.tensor([int(a)])).item() for f,a in zip(features,actions)])
 duplicated=L.torch.tensor([m([f,f])[0].log_prob(L.torch.tensor([int(a),int(a)]))[0].item() for f,a in zip(features,actions)])
 # Independent double-precision evaluation of exactly the same mathematical network.
 def reference(fs):
  g=L.torch.tensor([f['global'] for f in fs],dtype=L.torch.float64)
  width=max(len(f['mask']) for f in fs);c=L.torch.zeros(len(fs),width,m.dimensions[1],dtype=L.torch.float64)
  mask=L.torch.zeros(len(fs),width,dtype=L.torch.bool)
  for i,f in enumerate(fs):
   c[i,:len(f['mask'])]=L.torch.tensor(f['candidates'],dtype=L.torch.float64);mask[i,:len(f['mask'])]=L.torch.tensor(f['mask'],dtype=L.torch.bool)
  sd={k:v.double() for k,v in m.state_dict().items()}
  h=L.torch.tanh(L.torch.nn.functional.linear(g,sd['state.0.weight'],sd['state.0.bias']))
  x=L.torch.cat([h[:,None,:].expand(-1,width,-1),c],dim=-1)
  x=L.torch.tanh(L.torch.nn.functional.linear(x,sd['actor.0.weight'],sd['actor.0.bias']))
  logits=L.torch.nn.functional.linear(x,sd['actor.2.weight'],sd['actor.2.bias']).squeeze(-1)
  return L.Categorical(logits=logits.masked_fill(~mask,-L.torch.inf))
 refbatch=reference(features).log_prob(actions)
 refsingle=L.torch.stack([reference([f]).log_prob(L.torch.tensor([int(a)]))[0] for f,a in zip(features,actions)])
report=dict(actualOriginalUpdateVerdict=verdict,actualTwoStateWitness=True,parameterSHA256=before,actorParameterSHAMatches=True,
 checkpointSHA256=inputSHA,weightsUnchanged=L.digest(m)==before,storedActorLogprobs=old.tolist(),
 singletonError=(single-old).abs().tolist(),batchError=(batch-old).abs().tolist(),duplicateSameWidthBatchError=(duplicated-old).abs().tolist(),
 referenceFloat64BatchSingletonError=(refbatch-refsingle).abs().tolist(),
 states=[dict(candidates=len(f['mask']),legal=sum(f['mask']),action=int(a),selectedLegal=bool(f['mask'][a])) for f,a in zip(features,actions)])
assert L.digest(m)==before and hashlib.sha256(p.read_bytes()).hexdigest()==inputSHA
print(json.dumps(report,allow_nan=False));sys.exit(42 if 'mismatch' in verdict else 0)
