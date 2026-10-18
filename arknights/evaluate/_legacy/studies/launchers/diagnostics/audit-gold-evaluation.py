"""Read-only, after-run audit of the actual 96-game gold-feature study."""
from pathlib import Path
import hashlib
import json
import sys

root = Path('/data1/zsm/arknights')
code = root / 'experiment-code'
sys.path.insert(0, str(code / 'gold-study'))
from contracts import C, project

directory = root / 'experiments/gold-study-dev-v2'
summary_path = directory / 'summary.json'
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
summary_sha = digest(summary_path)
summary = json.loads(summary_path.read_bytes())
assert summary['status'] == 'complete' and summary['complete'] == 96 and summary['failed'] == 0
rows = summary['games']
assert len(rows) == 96 and len({r['id'] for r in rows}) == 96
assert {p.name for p in directory.iterdir()} == {'manifest.json','summary.json','admission.json'} | {r['id'] for r in rows}
count = 0
for row in rows:
    assert row['status'] == 'complete' and row['eof'] is True
    episode = directory / row['id']
    assert episode.is_dir() and not episode.is_symlink()
    assert {p.name for p in episode.iterdir()} == set(row['files']) | {'result.json'}
    for name, identity in {**row['files'], 'result.json':row['resultFile']}.items():
        file = episode / name
        assert file.is_file() and not file.is_symlink()
        assert file.stat().st_size == identity['bytes'] and digest(file) == identity['sha256']
        count += 1
    saved = json.loads((episode / 'result.json').read_bytes())
    assert saved == {k:v for k,v in row.items() if k != 'resultFile'}
    terminal = json.loads((episode / 'terminal.json').read_bytes())
    assert terminal['ok'] is True and terminal['done'] is True
    individual = C.terminal_contract(project(terminal, terminal['schema'], row['goldMode']),
                                    seed=row['seed'], steps=row['steps'],
                                    schema={**terminal['schema'],'version':'shop-v1'})
    assert terminal['summary']['victory'] is row['teamVictory']
    assert individual['victory'] is row['learnerIndividualVictory']
assert digest(summary_path) == summary_sha
out = root / 'experiments/gold-study-dev-v2-evidence-audit.json'
report = dict(status='complete', games=96, payloadFiles=count, summarySHA256=summary_sha,
              teamWins=sum(r['teamVictory'] for r in rows), individualWins=sum(r['learnerIndividualVictory'] for r in rows),
              scope='Postrun complete payload inventory, SHA, result equality, terminal teacher=False and survival audit; no new games.')
with out.open('x') as stream:
    json.dump(report, stream, indent=2)
print(json.dumps(report))
