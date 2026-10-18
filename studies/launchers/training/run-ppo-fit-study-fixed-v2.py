"""Finite paired online PPO from the same natural-win BC; original trainer and transport."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

root=Path('/data1/zsm/arknights')
code=root/'code-ppo-sync-v1'  # Immutable approved snapshot, also used by wide development.
sys.path.insert(0,str(code/'bc-fit-budget'))
import contracts_fit as F
C=F.C
spec=importlib.util.spec_from_file_location('ppo_timing_helpers',code/'prep-timing/run.py')
P=importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
out=root/'experiments/ppo-fit-timing-fixed-v2'
C.new_output(out)
transport=out/'transport'
transport.mkdir()
shutil.copyfile(root/'tools/capture-env.py',transport/'capture-env.py')
base=root/'experiments/bc-fit-budget-train-v1/steps3000.pt'
expected='af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b'
assert C.sha(base)==expected
config=json.loads((code/'rl/config.json').read_bytes())
config.update(gamma=1.0,lr=.0001,rollout_steps=64)
C.write(out/'config.json',config)
sources={str(p):C.sha(p) for d in ('rl','parallel-rl','coop','prep-timing','reward')
         for p in (code/d).iterdir() if p.is_file() and p.suffix in ('.py','.mjs','.js','.json')}
sources[str(transport/'capture-env.py')]=C.sha(transport/'capture-env.py')
sources[str(out/'config.json')]=C.sha(out/'config.json')
sources[str(Path(__file__).resolve())]=C.sha(Path(__file__))
source=P.source_identity()
game_path,game=P.game_identity(root)
clock=lambda:datetime.now(timezone.utc).isoformat()
manifest=dict(status='training',startedAt=clock(),kind='paired synchronous PPO after perfect-label-fit BC',
    source=source,sourceSHA256=sources,parentSHA256=expected,game=game,config=config,
    stepsPerArm=32768,actorsPerArm=4,trainSeedBase=60001,stride=4096,episodesPerActor=512,
    seedRanges=[dict(first=60001+i*4096,last=60001+i*4096+511) for i in range(4)],
    partnerPool='mixed',partners=['original-v1','buy2-v1','saver-v1'],validationUsed=False,
    reward='terminal team victory / failure; gamma1; no layer reward',
    limitations=['Training episodes may span behavior versions; never attribute them to a frozen model.',
                 'Known fixed heuristic partners; not learned FCP or unseen-family evidence.',
                 'Native arrange/draft retain privileged pool dependencies.'],arms={},plannedFrozenDevGames=128)
assert all(10001<=r['first']<=r['last']<90000 for r in manifest['seedRanges'])
def save():
    temp=out/'manifest.json.tmp'
    temp.write_bytes(C.encoded(manifest))
    temp.replace(out/'manifest.json')
def guard():
    assert P.source_identity()==source and P.game_identity(root)==(game_path,game)
    assert C.sha(base)==expected
    assert all(C.sha(Path(p))==h for p,h in sources.items())
def environment(mode):
    env={k:v for k,v in os.environ.items() if not k.startswith(('COOP_','ARK_','NODE_','STRONGHOLD_'))}
    env.update(COOP_POOL='mixed',COOP_SPLIT='train',COOP_POOL_SEED='313',
        COOP_TEAM='original-v1,buy2-v1,saver-v1',STRONGHOLD_GAME=str(game_path),
        PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='')
    return env
python=str(root/'venvs/learner-20261018/bin/python')
for mode,cpus in [('deferred','0-7')]:
    node=['env','ARK_PREP_TIMING='+mode,'node','--no-warnings','--loader',str(code/'prep-timing/loader.mjs'),
          str(code/'coop/env.mjs'),'--game',str(game_path)]
    proxy=[python,str(transport/'capture-env.py'),'--out',str(out/(mode+'-episodes')),'--',*node]
    command=['taskset','-c',cpus,python,'-B',str(code/'parallel-rl/train.py'),
        '--checkpoint',str(base),'--out',str(out/(mode+'.pt')),'--config',str(out/'config.json'),
        '--workers','4','--steps','32768','--rollout','64','--seed','60001','--episodes-per-worker','512',
        '--mode','coop','--difficulty','ABYSS','--env-command',shlex.join(proxy),'--reward-mode','terminal/v1',
        '--timeout','120','--batch-timeout','600','--provenance-file',str(code/'coop/pool.json')]
    manifest['arms'][mode]=dict(status='planned',command=command,cpuAffinity=cpus)
save()

def train(mode):
    started=clock()
    with (out/(mode+'.log')).open('x') as log:
        process=subprocess.Popen(manifest['arms'][mode]['command'],cwd=code,env=environment(mode),
                                 stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            result=process.wait(timeout=7200)
        except BaseException:
            process.terminate()  # Original trainer reaps its actors; PDEATHSIG covers a forced kill.
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            raise
    sidecar=out/(mode+'.pt.manifest.json')
    report=json.loads(sidecar.read_bytes()) if sidecar.exists() else {}
    assert result==0 and report['status']=='complete' and report['totalSteps']==32768, report.get('error')
    assert report['checkpointInput']['sha256']==expected and report['outputSHA256']==C.sha(out/(mode+'.pt'))
    updates=[json.loads(line) for line in (out/(mode+'.pt.jsonl')).open()]
    assert len(updates)==128 and [u['totalSteps'] for u in updates]==list(range(256,32769,256))
    assert sum(u['completedEpisodes'] for u in updates)==report['completedEpisodes']
    original=[json.loads(line) for line in (root/'experiments/ppo-fit-timing-v1/deferred.pt.jsonl').open()]
    keys=('update','behaviorVersion','behaviorSHA256','totalSteps','parameterSHA256','episodes','workerSteps','loss','behaviorLogprobMaxError')
    assert len(original)==37 and all(all(a[k]==b[k] for k in keys) for a,b in zip(original,updates[:37]))
    assert updates[37]['behaviorLogprobFallbackUsed'] is True and updates[37]['behaviorLogprobSingletonMaxError']==0.0
    assert all(u['behaviorLogprobAcceptedMaxError']<=2e-5 for u in updates)
    captures=list((out/(mode+'-episodes')).glob('*.json'))
    assert len(captures)==report['completedEpisodes']
    wins=[]
    for file in captures:
        episode=json.loads(file.read_bytes())
        terminal=episode['terminal']
        assert episode['controller']==P.VERSIONS[mode] and terminal['controller']==P.VERSIONS[mode]
        assert len(episode['actions'])==terminal['steps']
        own=C.terminal_contract(terminal,seed=terminal['episode']['seed'],steps=terminal['steps'],schema=terminal['schema'])
        assert terminal['episode']['partners']['split']=='train'
        if terminal['victory']:
            wins.append(dict(seed=terminal['episode']['seed'],path=str(file),sha256=C.sha(file),
                             teamVictory=True,individualVictory=own['victory'],individualAlive=own['alive']))
    guard()
    return dict(status='complete',startedAt=started,finishedAt=clock(),completedEpisodes=len(captures),
                trainingWins=wins,checkpointSHA256=C.sha(out/(mode+'.pt')),sidecarSHA256=C.sha(sidecar),
                first37UpdatesExactToOriginal=True,oldFailureUpdate38PassedOriginalThresholdViaSingleton=True,
                fallbackUpdates=sum(u['behaviorLogprobFallbackUsed'] for u in updates),
                note='Actual changing-policy training outcomes only; frozen development evaluation still required.')

try:
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending={executor.submit(train,mode):mode for mode in manifest['arms']}
        for future in as_completed(pending):
            result=future.result()
            manifest['arms'][pending[future]].update(result)
            save()
    guard()
    assert all(r['status']=='complete' for r in manifest['arms'].values())
    manifest['status']='training-complete-awaiting-frozen-dev'
except BaseException as error:
    manifest.update(status='failed',error=repr(error))
    raise
finally:
    manifest['finishedAt']=clock()
    save()
