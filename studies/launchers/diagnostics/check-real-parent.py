"""Exercise the actual designated parent and a real current-state sample, with no update."""
import gzip
import json
from pathlib import Path
import sys

root = Path('/data1/zsm/arknights')
code = root / 'experiment-code'
sys.path.insert(0, str(code / 'natural-win-bc'))
from support import C, game_identity, model_guard, check_parent, source_identity

parent = root / 'experiments/bc-expansion-v1/all.pt'
identity = 'f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a'
check_parent(root, parent, identity)
_, game = game_identity(root, code)
L = C.learner(code)
model, checkpoint = L.load_checkpoint(parent)
model_guard(L, model, checkpoint, game)
with gzip.open(root / 'experiments/natural-coop-teacher-search-v1/normal-30072.json.gz', 'rt') as stream:
    demo = json.load(stream)
features = demo['samples'][0]['features']
L.validate_checkpoint(checkpoint, dict(schema=game['schema'], rules=C.RULES, features=features))
before = L.digest(model)
model.eval()
action, logprob, value = model.act(features, greedy=True)
assert features['mask'][action] and L.digest(model) == before
check_parent(root, parent, identity)
receipt = dict(ok=True, kind='real parent validation and greedy forward only, zero optimizer or game steps',
               parentSHA256=identity, dimensions=model.dimensions, schemaEqual=True,
               rules=checkpoint['rules'], parameterSHA256=before,
               action=action, logprob=logprob, value=value, source=source_identity(code),
               gameTreeSHA256=game['treeSHA256'])
with (root / 'experiments/natural-win-real-parent-preflight-v1.json').open('x') as stream:
    json.dump(receipt, stream, indent=2)
print(json.dumps({k:v for k,v in receipt.items() if k != 'source'}))
