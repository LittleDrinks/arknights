"""Offline first divergence: real student protocol versus native teacher training inputs."""
from pathlib import Path
import gzip
import hashlib
import json

root = Path('/data1/zsm/arknights/experiments')
source = root / 'natural-coop-teacher-search-v1'
manifest = json.loads((source / 'manifest.json').read_bytes())
cases = [r for r in manifest['games'] if r.get('eligiblePositive')]
results = []
for case in cases:
    path = source / case['artifact']
    packed = path.read_bytes()
    assert hashlib.sha256(packed).hexdigest() == case['artifactSHA256']
    teacher = json.loads(gzip.decompress(packed))
    for name in ('parent', 'naturalWins'):
        directory = root / 'natural-win-bc-v3-seen-diagnostic' / f'{name}-{case["pool"]}-{case["seed"]}'
        item = dict(seed=case['seed'], pool=case['pool'], model=name, exactPrefixDecisions=0)
        with (directory / 'protocol.jsonl').open() as states, (directory / 'actions.jsonl').open() as actions:
            for j, sample in enumerate(teacher['samples']):
                state = json.loads(next(states))
                action = json.loads(next(actions))
                f, g = sample['features'], state['features']
                assert len(f['global']) == len(g['global']) == 5267
                if f != g or sample['round'] != state['rounds']:
                    item.update(firstDifference='input', decision=j, teacherRound=sample['round'],
                                policyRound=state['rounds'],
                                differingGlobalColumns=[i for i,(a,b) in enumerate(zip(f['global'],g['global'])) if a!=b],
                                candidateVectorsEqual=f['candidates']==g['candidates'], maskEqual=f['mask']==g['mask'])
                    break
                if action['candidateIndex'] != sample['action']:
                    item.update(firstDifference='action', decision=j, round=state['rounds'],
                                teacherActionIndex=sample['action'], policyActionIndex=action['candidateIndex'])
                    break
                item['exactPrefixDecisions'] += 1
            else:
                item['firstDifference'] = None
        results.append(item)
out = root / 'native-teacher-prefix-v1.json'
with out.open('x') as stream:
    json.dump(dict(kind='offline first-divergence diagnostic; no counterfactual claim', cases=results,
                   warning='Equal encoded inputs and selected indices do not prove identical actual actions or hidden game state.'), stream, indent=2)
print(json.dumps(results))
