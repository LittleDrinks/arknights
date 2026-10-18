"""Audit all frozen completed teacher responses, collecting every mismatch without training."""
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
import time

root = Path('/data1/zsm/arknights')
code = root / 'experiment-code'
sys.path.insert(0, str(code / 'natural-win-bc'))
from support import C, game_identity, pool_sha, source_identity
from admission import complete_contract, frozen, read_artifact

expected = 'dc8af9e7a99f96b2ec08dab3992c7512745ad84697770d632a904cbf7fb0463b'
directory = root / 'experiments/natural-coop-teacher-search-v1'

def check(row, game, pool):
    try:
        data, _, identity = read_artifact(directory / row['artifact'], row)
        selected = complete_contract(data, row, game, pool)
        return dict(seed=row['seed'], pool=row['pool'], ok=True, selected=selected,
                    labels=len(data['samples']) if selected else 0, artifactSHA256=identity['sha256'])
    except Exception as error:
        return dict(seed=row['seed'], pool=row['pool'], ok=False, error=f'{type(error).__name__}: {error}')

if __name__ == '__main__':
    started = time.monotonic()
    manifest, raw = frozen(directory / 'manifest.json', expected)
    declared = {r['artifact'] for r in manifest['games'] if 'artifact' in r}
    assert declared == {p.name for p in directory.glob('*.json.gz')}
    _, game = game_identity(root, code)
    source = source_identity(code)
    rows = [r for r in manifest['games'] if r['status'] == 'complete']
    assert len(rows) == 1486
    report = dict(status='running', kind='whole frozen corpus audit only, no model updates or simulations',
                  manifestSHA256=expected, source=source, game=game,
                  planned=2048, completeArtifacts=1486, collectorFailed=8, collectorUnrun=554, results=[])
    out = root / 'experiments/natural-win-bc-fullaudit-v2.json'
    assert not out.exists()
    def save():
        temp = out.with_suffix('.tmp')
        temp.write_text(json.dumps(report, indent=2) + '\n')
        temp.replace(out)
    save()
    with ProcessPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(check, r, game, pool_sha(code)) for r in rows]):
            report['results'].append(future.result())
            if len(report['results']) % 100 == 0:
                save()
    assert source_identity(code) == source and C.sha(directory / 'manifest.json') == expected
    assert declared == {p.name for p in directory.glob('*.json.gz')}
    report.update(status='complete' if all(r['ok'] for r in report['results']) else 'failed',
                  seconds=time.monotonic() - started, selectedGames=sum(r.get('selected', False) for r in report['results']),
                  selectedLabels=sum(r.get('labels', 0) for r in report['results']))
    save()
    print(json.dumps({k:v for k,v in report.items() if k not in ['source','game','results']}))
    print(json.dumps([r for r in report['results'] if not r['ok']]))
