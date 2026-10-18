"""Separate exact-index errors from identical encoded candidate rows; no policy updates."""
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

root = Path('/data1/zsm/arknights')
sys.path.insert(0, str(root / 'experiment-code'))
from rl.learner import load_checkpoint, digest
import torch

training = root / 'experiments/natural-win-bc-v3'
report = json.loads((training / 'train.json').read_text())
assert report['status'] == 'complete'
source = root / 'experiments/natural-coop-teacher-search-v1'
manifest = json.loads((source / 'manifest.json').read_text())
rows = [r for r in manifest['games'] if r.get('eligiblePositive')]
data = []
for row in rows:
    with gzip.open(source / row['artifact'], 'rt') as stream:
        demo = json.load(stream)
    data.extend((row, sample) for sample in demo['samples'])
assert len(data) == 498
results = {}
for name, file in [('parent', root / 'experiments/bc-expansion-v1/all.pt'), ('naturalWins', training / 'naturalWins.pt')]:
    model, _ = load_checkpoint(file)
    model.eval()
    before = digest(model)
    exact, feature_equal, duplicate_targets, confusion = 0, 0, 0, Counter()
    wrong = []
    with torch.no_grad():
        for start in range(0, len(data), 34):
            batch = data[start:start + 34]
            dist, _ = model([sample['features'] for _, sample in batch])
            for (row, sample), action in zip(batch, dist.logits.argmax(-1).tolist()):
                f, target = sample['features'], sample['action']
                chosen, expected = f['candidates'][action], f['candidates'][target]
                exact += action == target
                same = chosen == expected
                feature_equal += same
                duplicate_targets += sum(c == expected for c in f['candidates']) > 1
                if not same:
                    types = ['buy', 'sell', 'refresh', 'levelUp', 'ready']
                    a, b = types[expected[:5].index(1)], types[chosen[:5].index(1)]
                    confusion[a + '->' + b] += 1
                    wrong.append(dict(seed=row['seed'], pool=row['pool'], round=sample['round'],
                                      targetType=a, predictedType=b))
    assert digest(model) == before
    results[name] = dict(samples=498, exactIndexMatches=exact, identicalEncodedCandidateMatches=feature_equal,
                         labelsWithDuplicateEncodedCandidates=duplicate_targets,
                         differingFeatureRowConfusion=dict(confusion), differingFeatureRowErrors=wrong,
                         warning='Identical vectors do not prove interchangeable actions: UID/slot/equipment/position can differ.')
out = root / 'experiments/natural-label-fit-v1.json'
with out.open('x') as stream:
    json.dump(results, stream, indent=2)
print(json.dumps({n:{k:v for k,v in r.items() if k != 'differingFeatureRowErrors'} for n,r in results.items()}))
