"""Independent after-run payload and terminal audit; no new games or model updates."""
from pathlib import Path
import hashlib
import json
import sys

root=Path('/data1/zsm/arknights')
sys.path.insert(0,str(root/'experiment-code/survivor-study'))
import common as C
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
reports=[]
for name,n,controls in [('coop-items-dev-v2',128,{'manifest.json','summary.json'}),
                       ('bc-fit-budget-dev-v1',96,{'manifest.json','summary.json','admission.json'}),
                       ('bc-fit-timing-seen-v1',24,{'manifest.json','summary.json'}),
                       ('prep-timing-dev128-v1',128,{'manifest.json','summary.json'})]:
    if sys.argv[1:] and name not in sys.argv[1:]:
        continue
    out=root/'experiments'/name
    assert out.is_dir() and not out.is_symlink()
    file=out/'summary.json'
    initial=sha(file)
    summary=json.loads(file.read_bytes())
    assert summary['status']=='complete' and summary['complete']==n
    rows=summary['games']
    assert len(rows)==n and len({r['id'] for r in rows})==n
    assert {p.name for p in out.iterdir()}==controls|{r['id'] for r in rows}
    total=0
    for row in rows:
        assert row['status']=='complete' and row['eof'] is True
        directory=out/row['id']
        assert directory.is_dir() and not directory.is_symlink()
        files={**row['files'],'result.json':row['resultFile']} if 'resultFile' in row else summary['evidence'][row['id']]
        assert {p.name for p in directory.iterdir()}==set(files)
        for name_in,identity in files.items():
            p=directory/name_in
            assert p.is_file() and not p.is_symlink()
            assert p.stat().st_size==identity['bytes'] and sha(p)==identity['sha256']
            total+=1
        assert json.loads((directory/'result.json').read_bytes())=={k:v for k,v in row.items() if k!='resultFile'}
        t=json.loads((directory/'terminal.json').read_bytes())
        own=C.terminal_contract(t,seed=row['seed'],steps=row['steps'],schema=t['schema'])
        assert t['summary']['victory'] is row['teamVictory'] and own['victory'] is row['learnerIndividualVictory']
    assert sha(file)==initial
    report=dict(status='complete',experiment=name,games=n,payloadFiles=total,summarySHA256=initial,
                teamWins=sum(r['teamVictory'] for r in rows),individualWins=sum(r['learnerIndividualVictory'] for r in rows),
                scope='Postrun output inventory, regular directories/files, size/SHA, result equality and original terminal teacher=False.')
    with (root/'experiments'/(name+'-evidence-audit.json')).open('x') as stream:
        json.dump(report,stream,indent=2)
    reports.append(report)
print(json.dumps(reports))
