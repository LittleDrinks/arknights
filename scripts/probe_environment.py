#!/usr/bin/env python3
"""Bounded adapter equivalence probe, never a training run or success-rate study."""
import argparse,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--code',type=Path,required=True);p.add_argument('--game',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
env={k:v for k,v in os.environ.items() if not k.startswith(('ARK_','COOP_','STRONGHOLD_'))}
env.update(COOP_POOL='normal',COOP_SPLIT='eval',COOP_POOL_SEED='313',COOP_TEAM='original-v1,original-v1,original-v1')
commands=[dict(cmd='reset',seed=17,mode='coop',difficulty='ABYSS',budget=128)]+[dict(cmd='step',candidateIndex=0) for _ in range(6)]+[dict(cmd='close')]
r=subprocess.run(['node',str(a.code/'coop/env.mjs'),'--game',str(a.game)],input=''.join(json.dumps(x)+'\n' for x in commands).encode(),env=env,capture_output=True,timeout=30)
assert r.returncode==0 and not r.stderr,r.stderr[-2000:]
rows=[json.loads(x) for x in r.stdout.splitlines()];assert len(rows)==len(commands) and all(x['ok'] for x in rows)
a.out.parent.mkdir(parents=True,exist_ok=True)
with a.out.open('xb') as f:f.write(r.stdout)
print(json.dumps(dict(responses=len(rows),bytes=len(r.stdout),out=str(a.out))))
