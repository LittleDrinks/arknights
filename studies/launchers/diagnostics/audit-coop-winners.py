"""Audit reported training-winning seeds with frozen pre/post-training policies."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

root = Path('/data1/zsm/arknights')
out = root / 'experiments/coop-winner-audit-v1'
out.mkdir(exist_ok=False)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
clock = lambda: datetime.now(timezone.utc).isoformat()
models = {'bc': root / 'experiments/bc-expansion-v1/all.pt',
          **{n: root / 'experiments/coop-partner-ablation-v3' / (n + '.pt') for n in ('normal', 'mixed')}}
plan = [dict(name=n, pool=pool, seed=seed) for n in models for pool in ('normal', 'mixed') for seed in (50003, 50026)]
manifest = dict(status='running', startedAt=clock(), purpose='training-seed diagnostic; not unseen evaluation',
                plannedGames=12, checkpointSHA256={n: sha(p) for n, p in models.items()}, plan=plan, results=[])
program = '''
import sys,json,torch,hashlib
from pathlib import Path
sys.path.insert(0,"/data1/zsm/arknights/experiment-code")
from rl.learner import Environment,load_checkpoint,validate_checkpoint,episode_result
checkpoint,seed,destination=sys.argv[1:];seed=int(seed)
torch.set_num_threads(1);model,ckpt=load_checkpoint(checkpoint);model.eval()
env=Environment("node /data1/zsm/arknights/experiment-code/coop/env.mjs --game /data1/zsm/arknights/game")
actions=[]
try:
 state=env.request("reset",seed=seed,mode="coop",difficulty="ABYSS",budget=128);validate_checkpoint(ckpt,state)
 while not state["done"]:
  index,_,_=model.act(state["features"],greedy=True)
  actions.append({"round":state["rounds"],"candidateIndex":index,"action":state["candidates"][index]})
  state=env.request("step",candidateIndex=index)
 assert state["summary"]["victory"] is state["victory"]
 assert state["summary"]["seed"]==seed
 assert all(state[k]==0 for k in ["engineErrors","actionErrors"])
 Path(destination).write_text(json.dumps({"checkpointSHA256":hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),"actions":actions,"terminal":state})+"\\n")
 print(json.dumps(episode_result(state)))
finally:env.close()
'''


def save():
    temporary = out / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(out / 'manifest.json')


def run(row):
    label = f'{row["name"]}-{row["pool"]}-{row["seed"]}'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('ARK_', 'COOP_'))}
    env.update(COOP_POOL=row['pool'], COOP_SPLIT='eval', COOP_POOL_SEED='313',
               OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    if row['pool'] == 'mixed':
        env['COOP_TEAM'] = 'original-v1,buy2-v1,saver-v1'
    destination = out / (label + '.json')
    command = [str(root / 'venvs/learner-20261018/bin/python3'), '-c', program,
               str(models[row['name']]), str(row['seed']), str(destination)]
    record = dict(**row, label=label, startedAt=clock(), status='failed')
    result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    (out / (label + '.log')).write_text(result.stdout)
    record.update(exitCode=result.returncode, finishedAt=clock())
    if result.returncode == 0:
        data = json.loads(destination.read_text())
        terminal = data['terminal']
        record.update(status='complete', victory=terminal['victory'], rounds=terminal['rounds'],
                      engineErrors=terminal['engineErrors'], actionErrors=terminal['actionErrors'],
                      controllerErrors=terminal['controllerErrors'],
                      finalReason=terminal['summary'].get('reason'), artifactSHA256=sha(destination))
    return record


save()
try:
    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(run, row) for row in plan]):
            record = future.result()
            manifest['results'].append(record)
            save()
            print(json.dumps(record), flush=True)
    if any(sha(models[n]) != value for n, value in manifest['checkpointSHA256'].items()):
        raise RuntimeError('Checkpoint changed during diagnostic')
    manifest['status'] = 'complete' if all(r['status'] == 'complete' for r in manifest['results']) else 'failed'
finally:
    manifest['finishedAt'] = clock()
    save()
