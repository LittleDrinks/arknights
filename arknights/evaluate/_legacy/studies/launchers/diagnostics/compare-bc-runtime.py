"""Compare complete real-data BC checkpoints; cache acceleration is not skill gain."""
import hashlib
import json
from pathlib import Path
import torch

torch.set_num_threads(1)
root = Path('/data1/zsm/arknights')
slow = root / 'experiment-code/interaction/server-paired-16k-v1'
fast = root / 'experiment-code/tensor-bc/server-paired-16k-v1'
out = root / 'experiments/bc-runtime-equivalence-v1.json'
assert not out.exists()
a, b = [json.loads((p / 'train.json').read_text()) for p in (slow, fast)]
assert a['status'] == b['status'] == 'complete'
assert a['config'] == b['config']
assert a['dataset']['datasetSHA256'] == b['dataset']['datasetSHA256']
assert a['epochOrderSHA256'] == b['epochOrderSHA256']
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
arms = []
for x, y in zip(a['arms'], b['arms'], strict=True):
    assert x['architecture'] == y['architecture']
    assert x['initialParameterSHA256'] == y['initialParameterSHA256']
    assert x['metrics'] == y['metrics']
    paths = [d / v['checkpoint'] for d, v in ((slow, x), (fast, y))]
    assert all(sha(p) == v['checkpointSHA256'] for p, v in zip(paths, (x, y)))
    ckpts = [torch.load(p, map_location='cpu', weights_only=True) for p in paths]
    assert ckpts[0]['architecture'] == ckpts[1]['architecture']
    assert ckpts[0]['schema'] == ckpts[1]['schema']
    assert ckpts[0]['rules'] == ckpts[1]['rules']
    states = [c['policyState'] for c in ckpts]
    assert states[0].keys() == states[1].keys()
    assert all(torch.equal(states[0][k], states[1][k]) for k in states[0])
    arms.append(dict(architecture=x['architecture'], parameterSHA256=x['metrics']['parameterSHA256'],
                     allStateTensorsBitwiseEqual=True, tensorCount=len(states[0]),
                     slowTrainingSeconds=x['seconds'], fastTrainingSeconds=y['seconds'],
                     trainingOnlySpeedup=x['seconds'] / y['seconds'],
                     slowCheckpointSHA256=x['checkpointSHA256'], fastCheckpointSHA256=y['checkpointSHA256']))
result = dict(kind='real-data-BC-runtime-equivalence/v1', ok=True,
              config=a['config'], datasetSHA256=a['dataset']['datasetSHA256'],
              epochOrderSHA256=a['epochOrderSHA256'], arms=arms,
              fastAdmissionAndCacheSeconds=b['source']['tensorCache']['admissionAndCacheSeconds'],
              warning='Measured training runtime, not victory improvement; input admission/cache time is separate.',
              sourceSHA256=sha(Path(__file__)), manifests={str(p / 'train.json'): sha(p / 'train.json') for p in (slow, fast)})
with out.open('x') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
print(json.dumps(result))
