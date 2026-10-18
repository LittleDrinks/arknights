"""Independently repeat two collected natural individual victories, without fitting a model."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

root = Path('/data1/zsm/arknights')
os.environ.update(OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
sys.path.insert(0, str(root / 'experiment-code'))
from rl.learner import Environment

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
source = root / 'experiments/natural-coop-teacher-search-v1'
manifest = json.loads((source / 'manifest.json').read_text())
hashes = manifest['sourceSHA256']
assert all(sha(p) == identity for p, identity in hashes.items())
results = []
for pool, seed in [('normal', 30072), ('mixed', 30122)]:
    file = source / f'{pool}-{seed}.json.gz'
    with gzip.open(file, 'rt') as stream:
        original = json.load(stream)
    team = 'original-v1,original-v1,original-v1' if pool == 'normal' else 'original-v1,buy2-v1,saver-v1'
    command = shlex.join(['env', 'COOP_POOL=' + pool, 'COOP_SPLIT=train', 'COOP_POOL_SEED=313', 'COOP_TEAM=' + team,
                         'node', str(root / 'experiment-code/coop/env.mjs'), '--game', str(root / 'game')])
    with Environment(command, timeout=60) as env:
        repeat = env.request('demo', seed=seed, mode='coop', difficulty='ABYSS', budget=128)
    assert repeat == original, f'Full raw demo changed on independent repeat: {pool}/{seed}'
    terminal = repeat['terminal']
    player = next(p for p in terminal['summary']['players'] if p['playerId'] == 'learner')
    assert player['alive'] is True and player['victory'] is True and player['roundsPassed'] == 14
    assert terminal['victory'] is True and repeat['rejectedLabels'] == 0
    assert all(terminal[k] == 0 for k in ('engineErrors', 'actionErrors', 'controllerErrors'))
    results.append(dict(pool=pool, seed=seed, fullRawDemoEqual=True, completeTerminalEqual=True,
                        individualVictory=True, teamVictory=True, samples=len(repeat['samples']),
                        inputSHA256=sha(file), terminalSHA256=hashlib.sha256(json.dumps(terminal,sort_keys=True).encode()).hexdigest()))
assert all(sha(p) == identity for p, identity in hashes.items())
out = root / 'experiments/natural-teacher-verify-v1.json'
with out.open('x') as stream:
    json.dump(dict(ok=True, kind='independent exact natural teacher replay, not learned model performance',
                   results=results, sourceSHA256=hashes, verificationSourceSHA256=sha(Path(__file__))), stream, indent=2)
    stream.write('\n')
print(json.dumps(results))
