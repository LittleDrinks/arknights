"""Seen-source diagnostic: can a frozen shopping policy reproduce natural teacher successes?"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import time

root = Path('/data1/zsm/arknights')
code = root / 'experiment-code'
sys.path.insert(0, str(code / 'natural-win-bc'))
from support import C, E, game_identity, pool_sha, source_identity

out = root / 'experiments/natural-win-bc-v3-seen-diagnostic'
out.mkdir(exist_ok=False)
training = root / 'experiments/natural-win-bc-v3'
train = json.loads((training / 'train.json').read_text())
assert train['status'] == 'complete'
paths = dict(parent=root / 'experiments/bc-expansion-v1/all.pt', naturalWins=training / 'naturalWins.pt')
hashes = {n: C.sha(p) for n, p in paths.items()}
assert hashes['naturalWins'] == train['result']['checkpointSHA256']
manifest = json.loads((root / 'experiments/natural-coop-teacher-search-v1/manifest.json').read_text())
cases = [r for r in manifest['games'] if r.get('eligiblePositive')]
assert len(cases) == 6 and {r['seed'] for r in cases} == set(train['trainingEpisodeGroups'])
rows = [dict(id=f'{name}-{r["pool"]}-{r["seed"]}', model=name,
             pool='original' if r['pool'] == 'normal' else 'mixed', seed=r['seed'], mode='coop',
             difficulty='ABYSS', seat=0, status='unrun', teamVictory=None, learnerIndividualVictory=None)
        for name in paths for r in cases]
source = source_identity(code)
_, game = game_identity(root, code)
report = dict(status='running', kind='six seen teacher-win seeds; not generalization', planned=12,
              difference='Teachers use native whole prep; learned policies use the declared fixed deployment controller.',
              source=source, checkpointSHA256=hashes, games=rows)
def save():
    temp = out / 'manifest.json.tmp'
    temp.write_text(json.dumps(report, indent=2) + '\n')
    temp.replace(out / 'manifest.json')
save()
L = C.learner(code)
models = {name: L.load_checkpoint(path) for name, path in paths.items()}
for model, _ in models.values():
    model.eval()
    model.requires_grad_(False)
parameter_hashes = {name: L.digest(pair[0]) for name, pair in models.items()}
pool = pool_sha(code)
with ThreadPoolExecutor(max_workers=4) as executor:
    pending = {executor.submit(E.game_job, row, *models[row['model']], code, root / 'game', out,
                               time.monotonic() + 120, 64 * 1024**2, pool): index
               for index, row in enumerate(rows)}
    for future in as_completed(pending):
        index = pending[future]
        try:
            rows[index] = future.result()
        except Exception as error:
            rows[index].update(status='failed', error=repr(error))
        save()
assert source_identity(code) == source and game_identity(root, code)[1] == game
assert all(C.sha(paths[n]) == h for n, h in hashes.items())
assert all(L.digest(models[n][0]) == h for n, h in parameter_hashes.items())
for row in rows:
    if 'files' in row:
        for name, identity in row['files'].items():
            file = out / row['id'] / name
            assert file.stat().st_size == identity['bytes'] and C.sha(file) == identity['sha256']
report.update(status='complete' if all(r['status'] == 'complete' for r in rows) else 'failed',
              teamWins=sum(r.get('teamVictory') is True for r in rows),
              individualWins=sum(r.get('learnerIndividualVictory') is True for r in rows))
save()
print(json.dumps([{k:r.get(k) for k in ('id','status','teamVictory','learnerIndividualVictory','rounds','error')} for r in rows]))
