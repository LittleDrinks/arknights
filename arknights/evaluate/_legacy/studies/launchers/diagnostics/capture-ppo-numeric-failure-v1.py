"""Repeat exact failed arm prefix with exception-only observation, no algorithm changes."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
root=Path('/data1/zsm/arknights')
original=root/'code-wide-dev-v1'
code=root/'code-numeric-debug-v1'
out=root/'experiments/ppo-numeric-capture-v1'
assert not code.exists() and not out.exists()
subprocess.run(['git','clone','--no-hardlinks','--no-checkout',str(original),str(code)],check=True,capture_output=True)
subprocess.run(['git','-C',str(code),'checkout','--detach','4b6069b1e2000f6e4b62195c488a3e78c285dd2c'],check=True,capture_output=True)
file=code/'parallel-rl/train.py';before=file.read_text()
needle='                metrics = update(model, optimizer, results, config)\n'
assert before.count(needle)==1
replacement='''                try:
                    metrics = update(model, optimizer, results, config)
                except RuntimeError as diagnostic_error:
                    if 'Behavior policy mismatch:' in str(diagnostic_error):
                        torch.save(dict(kind='actual-failed-ppo-sync-rollout/v1', model=model.state_dict(),
                                        dimensions=model.dimensions, results=results, config=config,
                                        behaviorSHA256=policy_sha, behaviorVersion=version,
                                        totalCompletedSteps=total, originalError=str(diagnostic_error)),
                                   output.with_name(output.name + '.numeric-failure.pt'))
                    raise
'''
file.write_text(before.replace(needle,replacement))
assert file.read_text().replace(replacement,needle)==before
out.mkdir()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'config.json').write_bytes((root/'experiments/ppo-fit-timing-v1/config.json').read_bytes())
transport=out/'transport';transport.mkdir()
(transport/'capture-env.py').write_bytes((root/'experiments/ppo-fit-timing-v1/transport/capture-env.py').read_bytes())
python=str(root/'venvs/learner-20261018/bin/python')
node=['env','ARK_PREP_TIMING=deferred','node','--no-warnings','--loader',str(code/'prep-timing/loader.mjs'),str(code/'coop/env.mjs'),'--game',str(root/'game')]
proxy=[python,str(transport/'capture-env.py'),'--out',str(out/'episodes'),'--',*node]
cmd=['taskset','-c','0-7',python,'-B',str(file),'--checkpoint',str(root/'experiments/bc-fit-budget-train-v1/steps3000.pt'),
 '--out',str(out/'deferred.pt'),'--config',str(out/'config.json'),'--workers','4','--steps','9728','--rollout','64',
 '--seed','60001','--episodes-per-worker','512','--mode','coop','--difficulty','ABYSS','--env-command',shlex.join(proxy),
 '--reward-mode','terminal/v1','--timeout','120','--batch-timeout','600','--provenance-file',str(code/'coop/pool.json')]
env={k:v for k,v in os.environ.items() if not k.startswith(('COOP_','ARK_','NODE_','STRONGHOLD_'))}
env.update(COOP_POOL='mixed',COOP_SPLIT='train',COOP_POOL_SEED='313',COOP_TEAM='original-v1,buy2-v1,saver-v1',
 STRONGHOLD_GAME=str(root/'game'),PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='')
manifest=dict(kind='observational-original-numerical-failure-prefix/v1',status='running',maxSeconds=1800,
 originalTrainSHA256=sha(original/'parallel-rl/train.py'),instrumentedTrainSHA256=sha(file),sourceSnapshot='4b6069b',
 scriptSHA256=sha(Path(__file__)),observation='Only exception capture added around original update; same RNG, optimizer, sampling and threshold',
 algorithmChanged=False,trainingAllowed=False,command=cmd)
(out/'manifest.json').write_text(json.dumps(manifest,indent=2))
start=time.monotonic()
with (out/'run.log').open('x') as log:
 process=subprocess.Popen(cmd,cwd=code,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:rc=process.wait(timeout=1800)
 except BaseException:
  process.terminate()
  try:process.wait(timeout=20)
  except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
  raise
failure=out/'deferred.pt.numeric-failure.pt'
old=[json.loads(x) for x in (root/'experiments/ppo-fit-timing-v1/deferred.pt.jsonl').open()]
new=[json.loads(x) for x in (out/'deferred.pt.jsonl').open()]
keys=('update','behaviorVersion','behaviorSHA256','totalSteps','parameterSHA256','episodes','workerSteps','loss','behaviorLogprobMaxError')
prefix= len(old)==len(new)==37 and all(all(a[k]==b[k] for k in keys) for a,b in zip(old,new))
manifest.update(status='captured' if failure.exists() and prefix else 'failed',returncode=rc,seconds=time.monotonic()-start,
 first37UpdatesExact=prefix,originalUpdateCount=len(old),newUpdateCount=len(new),failureBytes=failure.stat().st_size if failure.exists() else 0,
 failureSHA256=sha(failure) if failure.exists() else None)
(out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest))
assert manifest['status']=='captured',manifest
