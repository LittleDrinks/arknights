"""Finite training-only natural teacher search, with every full response retained."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time

root = Path('/data1/zsm/arknights')
os.environ.update(OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
for key in list(os.environ):
    if key.startswith(('COOP_', 'ARK_')):
        del os.environ[key]
sys.path.insert(0, str(root / 'experiment-code'))
from rl.learner import Environment

out = root / 'experiments/natural-coop-teacher-search-v1'
out.mkdir(exist_ok=False)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
clock = lambda: datetime.now(timezone.utc).isoformat()
sources = {str(p): sha(p) for d in ('rl', 'coop') for p in (root / 'experiment-code' / d).iterdir()
           if p.suffix in ('.py', '.mjs', '.json') and p.is_file()}
sources[str(Path(__file__).resolve())] = sha(Path(__file__))
rows = [dict(seed=seed, pool=pool, status='not-run') for seed in range(30001, 31025) for pool in ('normal', 'mixed')]
manifest = dict(status='running', kind='natural ABYSS training-seed teacher search, not model or heldout evaluation',
                startedAt=clock(), planned=2048, pairedTrainingSeeds=1024, workers=8,
                primaryMetric='complete natural ABYSS team victory; individual learner survival reported separately',
                seedSplit='training only 30001..31024, all map seeds paired across pools; no heldout data',
                sourceSHA256=sources, games=rows)
deadline = time.monotonic() + 1800
def save():
    temp = out / 'manifest.json.tmp'
    temp.write_text(json.dumps(manifest, indent=2) + '\n')
    temp.replace(out / 'manifest.json')
save()

def collect(pool, lane):
    # Declare the fixed partner environment to the child only; no process-wide mutations in threads.
    team = 'original-v1,original-v1,original-v1' if pool == 'normal' else 'original-v1,buy2-v1,saver-v1'
    command = shlex.join(['env', 'COOP_POOL=' + pool, 'COOP_SPLIT=train', 'COOP_POOL_SEED=313', 'COOP_TEAM=' + team,
                         'node', '--max-old-space-size=768', str(root / 'experiment-code/coop/env.mjs'), '--game', str(root / 'game')])
    env = Environment(command, timeout=120)
    try:
        for index, row in enumerate(rows):
            if row['pool'] != pool or (row['seed'] - 30001) % 4 != lane:
                continue
            if time.monotonic() >= deadline:
                break
            result = dict(row, startedAt=clock())
            try:
                env.timeout = min(120, max(.001, deadline - time.monotonic()))
                data = env.request('demo', seed=row['seed'], mode='coop', difficulty='ABYSS', budget=128)
                # Persist the entire unfiltered terminal and all teacher labels BEFORE admission.
                file = out / f"{pool}-{row['seed']}.json.gz"
                raw = (json.dumps(data, allow_nan=False) + '\n').encode()
                assert len(raw) <= 64 * 1024 * 1024, 'bounded full response'
                with file.open('xb') as stream:
                    stream.write(gzip.compress(raw, compresslevel=3))
                result.update(artifact=file.name, artifactSHA256=sha(file), bytes=file.stat().st_size)
                t = data['terminal']
                assert t['done'] is True and t['episode']['seed'] == row['seed'] and t['episode']['mode'] == 'coop'
                assert t['episode']['difficulty'] == 'ABYSS' and t['episode']['budget'] == 128
                assert t['summary']['victory'] is t['victory'] and t['summary']['seed'] == row['seed']
                assert t['summary']['modeId'] == 'mode_multi_abyss' and t['summary']['errors'] == 0
                assert t['rules'] == '6ab2468279e926d107788f63d7fe219b85f67b4a'
                assert all(t[k] == 0 for k in ('engineErrors', 'actionErrors'))
                assert t['controllerErrors'] == t['controllerDiagnosticErrors']
                assert len(t['summary']['players']) == 4 and t['rounds'] == t['summary']['roundsPassed']
                ps = next(p for p in t['summary']['players'] if p['playerId'] == 'learner')
                for sample in data['samples']:
                    f, a = sample['features'], sample['action']
                    assert type(a) is int and 0 <= a < len(f['mask']) and f['mask'][a] is True
                result.update(status='complete', teamVictory=t['victory'], learnerVictory=ps['victory'],
                              learnerAlive=ps['alive'], rounds=t['rounds'], learnerRounds=ps['roundsPassed'],
                              rejectedLabels=data['rejectedLabels'], samples=len(data['samples']),
                              eligiblePositive=t['victory'] and ps['victory'] and ps['alive'] and
                              ps['roundsPassed'] == 14 and data['rejectedLabels'] == 0)
            except Exception as exc:
                result.update(status='failed', error=repr(exc)[:2000])
                env.close()
                env = Environment(command, timeout=120)
            result['finishedAt'] = clock()
            yield index, result
    finally:
        env.close()

def lane_task(pool, lane):
    # Workers return only their own rows. Main alone publishes manifest snapshots.
    return list(collect(pool, lane))

try:
    with ThreadPoolExecutor(max_workers=8) as executor:
        for future in as_completed([executor.submit(lane_task, pool, lane) for pool in ('normal', 'mixed') for lane in range(4)]):
            for index, result in future.result():
                rows[index] = result
            save()
            print(json.dumps(dict(complete=sum(r['status'] == 'complete' for r in rows),
                                  failed=sum(r['status'] == 'failed' for r in rows),
                                  teamWins=sum(r.get('teamVictory', False) for r in rows),
                                  eligiblePositives=sum(r.get('eligiblePositive', False) for r in rows))), flush=True)
    assert all(sha(p) == identity for p, identity in sources.items()), 'Source changed during collection'
    manifest['status'] = 'complete' if all(r['status'] == 'complete' for r in rows) else 'incomplete'
except BaseException as error:
    manifest.update(status='failed', error=repr(error))
    raise
finally:
    manifest['finishedAt'] = clock()
    save()
