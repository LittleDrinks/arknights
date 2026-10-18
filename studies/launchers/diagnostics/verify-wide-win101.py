"""Independent frozen inference reproduction of the candidate seed101 team victory and paired loss."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import time

root=Path('/data1/zsm/arknights')
code=root/'code-wide-dev-v1'
sys.path.insert(0,str(code/'wide-dev'))
import contracts_wide as W
import admission_wide as A
import evaluation as V
P,C,F=W.P,W.C,W.F
out=root/'experiments/wide-seed101-reproduction-v1'
C.new_output(out)
source=W.source_identity(code)
game_path,game,pin=F.game_identity(root,code)
L=C.learner(code)
models,admitted,check_training=A.admit(L,root/'experiments/bc-fit-budget-train-v1',F.check_parent(root),
    'af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b',
    '8a7e6c600066c8b6fb7c8606375f217961514141396d3b24260cea3df073e2ff',source['fit'],game,pin)
rows=[r for r in V.schedule() if r['seed']==101 and r['controller']=='deferred' and r['pool']=='original']
assert len(rows)==2
P.write_new(out/'manifest.json',dict(kind='frozen-policy inference reproducibility; previously exposed dev seed101',
    games=rows,source=source,admission=admitted,validationUsed=False,wholeWideRunFinalized=False))
manifest_identity=P.seal_manifest(out)
deadline=time.monotonic()+180
seals={}
with ThreadPoolExecutor(max_workers=2) as executor:
    pending={executor.submit(P.game_job,r,*models[r['model']],game_path,out,
        time.monotonic()+120,64*F.MIB,F.G.pool_sha(code)):i for i,r in enumerate(rows)}
    for future in as_completed(pending):
        i=pending[future]
        rows[i],seals[rows[i]['id']]=future.result()
check_training(deadline)
assert W.source_identity(code)==source and F.game_identity(root,code)==(game_path,game,pin)
P.audit_evidence(out,rows,seals,manifest_identity,64*F.MIB,deadline)
comparisons=[]
for row in rows:
    assert row['status']=='complete'
    original=root/'experiments/wide-dev-v1'/row['id']
    copied=out/row['id']
    assert original.is_dir() and not original.is_symlink()
    identical={}
    for name in ('protocol.jsonl','actions.jsonl','terminal.json'):
        identical[name]=C.sha(original/name)==C.sha(copied/name)
        assert identical[name],(row['id'],name)
    comparisons.append(dict(id=row['id'],teamVictory=row['teamVictory'],
        individualVictory=row['learnerIndividualVictory'],learnerOutcome=row['learnerOutcome'],
        exactPayloads=identical))
assert rows[0]['model']=='parent' and rows[0]['teamVictory'] is False
assert rows[1]['model']=='steps3000' and rows[1]['teamVictory'] is True
candidate=rows[1]
directory=out/candidate['id']
terminal=json.loads((directory/'terminal.json').read_bytes())
actions=[json.loads(line) for line in (directory/'actions.jsonl').open()]
trace=dict(kind='frozen-policy-evaluation-trajectory/v1',rules=C.RULES,controller=terminal['controller'],budget=128,
    behavior='One frozen steps3000 BC checkpoint; team victory with learner alive R14; learner Boss damage zero; partner Boss contribution; development only.',
    checkpointSHA256=admitted['candidate']['sha256'],actions=actions,terminal=terminal,
    originalResultSHA256=C.sha(root/'experiments/wide-dev-v1'/candidate['id']/'result.json'),
    reproductionManifestSHA256=C.sha(out/'manifest.json'),captureSourceSHA256=C.sha(Path(__file__)))
P.write_new(root/'experiments/wide-seed101-frozen-trace-v1.json',trace,12_000_000)
summary=dict(status='complete',inferenceRerun=True,wholeWideRunFinalized=False,
    source=source,comparisons=comparisons,traceSHA256=C.sha(root/'experiments/wide-seed101-frozen-trace-v1.json'),
    games=rows,evidence=seals,manifest=manifest_identity)
P.write_new(out/'summary.json',summary)
print(json.dumps(comparisons))
