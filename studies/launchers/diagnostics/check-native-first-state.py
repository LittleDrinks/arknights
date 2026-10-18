"""Offline: compare native teacher's first shopping input with fixed-controller reset.

This does not assert that equal encoded features imply equal hidden game state.
"""
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
    sample = teacher['samples'][0]
    for name in ('parent', 'naturalWins'):
        directory = root / 'natural-win-bc-v3-seen-diagnostic' / f'{name}-{case["pool"]}-{case["seed"]}'
        with (directory / 'protocol.jsonl').open() as stream:
            state = json.loads(next(stream))
        with (directory / 'actions.jsonl').open() as stream:
            action = json.loads(next(stream))
        f, g = sample['features'], state['features']
        assert len(f['global']) == len(g['global']) == 5267
        assert teacher['schema'] == state['schema'] and sample['round'] == state['rounds'] == 1
        target = f['candidates'][sample['action']]
        matching = [i for i, vector in enumerate(g['candidates']) if vector == target]
        results.append(dict(seed=case['seed'], pool=case['pool'], model=name,
                            firstGlobalEqual=f['global'] == g['global'],
                            differingGlobalColumns=[i for i, (a,b) in enumerate(zip(f['global'],g['global'])) if a != b],
                            candidateVectorsEqual=f['candidates'] == g['candidates'],
                            teacherCandidateCount=len(f['candidates']), policyCandidateCount=len(g['candidates']),
                            teacherTargetAvailableAsIdenticalVector=matching,
                            teacherActionIndex=sample['action'], policyActionIndex=action['candidateIndex'],
                            policyFirstActionMatchesTargetVector=g['candidates'][action['candidateIndex']] == target))
out = root / 'native-teacher-first-state-v1.json'
with out.open('x') as stream:
    json.dump(dict(kind='offline first-input comparison only', cases=results,
                   warning='Encoded equality does not prove equal game states, item/position decisions or RNG.'), stream, indent=2)
print(json.dumps(results))
