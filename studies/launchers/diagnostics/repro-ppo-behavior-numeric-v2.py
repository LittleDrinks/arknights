"""Diagnostic only: real original update guard, frozen failed checkpoint, no optimizer mutation."""
import importlib.util
import json
from pathlib import Path
import sys
import time
import hashlib

root=Path('/data1/zsm/arknights')
code=root/'code-wide-dev-v1'
sys.path.insert(0,str(code))
from rl import learner as L
spec=importlib.util.spec_from_file_location('original_parallel_train',code/'parallel-rl/train.py')
T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)
path=root/'experiments/ppo-fit-timing-v1/deferred.pt'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
input_hash=sha(path)
model,checkpoint=L.load_checkpoint(path)
model.eval()
before=L.digest(model)
features=[]
for name in tuple(f'steps3000-deferred-original-{seed:03}' for seed in range(1,25)):
 with (root/'experiments/wide-dev-v1'/name/'protocol.jsonl').open() as stream:
  for line in stream:
   state=json.loads(line)
   if 'features' in state and not state.get('done'):
    features.append(state['features'])
features=features[:2048]
config=json.loads((root/'experiments/ppo-fit-timing-v1/config.json').read_bytes())
class GuardPassed(Exception):pass
optimizer=L.torch.optim.SGD(model.parameters(),lr=0.0)
def no_step():raise GuardPassed('Guard accepted; diagnostic stops before any optimizer step')
optimizer.step=no_step
results=[]
started=time.monotonic()
with L.torch.no_grad():
 singleton=[model([f])[0].logits[0].clone() for f in features]
for start in range(0,len(features),32):
 batch=features[start:start+32]
 with L.torch.no_grad():
  dist,values=model(batch)
 actions=[];old=[];max_error=0.0;max_probability=0.0
 for i,f in enumerate(batch):
  single=singleton[start+i]
  allowed=L.torch.tensor(f['mask'],dtype=L.torch.bool)&(single.exp()>0)
  delta=(single-dist.logits[i,:len(single)]).abs().masked_fill(~allowed,-1)
  a=int(delta.argmax().item());error=float(delta[a])
  actions.append(a);old.append(float(single[a]))
  if error>max_error:max_error=error;max_probability=float(single[a].exp())
 n=len(batch)
 trajectory=dict(features=batch,actions=actions,logprobs=old,values=values.tolist(),rewards=[0.0]*n,
                 dones=[False]*n,ends=[False]*(n-1)+[True],next_values=[0.0]*n)
 try:
  T.update(model,optimizer,[dict(worker=0,trajectory=trajectory)],config)
  raise AssertionError('Diagnostic unexpectedly completed optimizer')
 except GuardPassed:
  verdict='guard-passed'
 except RuntimeError as exc:
  if 'Behavior policy mismatch:' not in str(exc):raise
  verdict=str(exc)
 results.append(dict(start=start,states=n,maxError=max_error,witnessProbability=max_probability,verdict=verdict))
print(json.dumps(dict(kind='numerical-sync-guard-diagnostic/v1',checkpointSHA256=input_hash,
 modelParametersUnchanged=L.digest(model)==before,checkpointUnchanged=sha(path)==input_hash,
 actualOriginalUpdate=True,originalFailureUpdateReplay=False,actions='legal positive-probability numeric witnesses, not sampled original failed rollout',
 states=len(features),batches=results,seconds=time.monotonic()-started),allow_nan=False))
assert L.digest(model)==before and sha(path)==input_hash
if any('Behavior policy mismatch:' in x['verdict'] for x in results):sys.exit(42)
