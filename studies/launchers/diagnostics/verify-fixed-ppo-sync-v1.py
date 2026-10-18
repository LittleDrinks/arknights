import importlib.util,json,sys
from pathlib import Path
root=Path('/data1/zsm/arknights');sys.path.insert(0,str(root/'code-ppo-sync-v1'))
from rl import learner as L
mods=[]
for name,folder in [('old','code-wide-dev-v1'),('new','code-ppo-sync-v1')]:
 spec=importlib.util.spec_from_file_location(name,root/folder/'parallel-rl/train.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);mods.append(mod)
witness=root/'experiments/ppo-numeric-capture-v1/minimal-sync-witness-v1.pt';d=L.torch.load(witness,map_location='cpu',weights_only=False)
class GuardPassed(Exception):pass
results=[]
for name,mod in zip(('old','new'),mods):
 m=L.ActorCritic(*d['dimensions']);m.load_state_dict(d['model']);m.eval();before=L.digest(m)
 optimizer=L.torch.optim.SGD(m.parameters(),lr=0.0)
 def no_step():raise GuardPassed()
 optimizer.step=no_step
 try:mod.update(m,optimizer,d['results'],d['config']);raise AssertionError('Unexpected update completion')
 except GuardPassed:verdict='guard-accepted'
 except RuntimeError as error:
  if 'Behavior policy mismatch:' not in str(error):raise
  verdict=str(error)
 assert L.digest(m)==before==d['behaviorSHA256']
 results.append(dict(version=name,verdict=verdict,parametersUnchanged=True))
assert 'Behavior policy mismatch:' in results[0]['verdict'] and results[1]['verdict']=='guard-accepted'
# Real optimizer on private model copy to record actual fallback diagnostics; nothing saved/promoted.
m=L.ActorCritic(*d['dimensions']);m.load_state_dict(d['model']);m.eval()
optimizer=L.torch.optim.Adam(m.parameters(),lr=d['config']['lr']);L.torch.manual_seed(101)
metrics=mods[1].update(m,optimizer,d['results'],d['config'])
report=dict(status='complete',actualSameCPUOriginalRedFixedGreen=True,results=results,metrics=metrics,
 realOptimizerPrivateCopyOnly=True,checkpointSaved=False)
(root/'experiments/ppo-numeric-capture-v1/fixed-witness-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
