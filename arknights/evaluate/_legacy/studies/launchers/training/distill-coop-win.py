"""Recover current-state labels from a verified successful training episode.

Single-episode fitting is deliberately a retention experiment on a seen training
seed, not a generalization claim. All collection uses original reset/step.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys

root = Path('/data1/zsm/arknights')
sys.path.insert(0, str(root / 'experiment-code'))
os.environ.update(OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
import torch
from rl.learner import Environment, load_checkpoint, validate_checkpoint, bc_update, save_checkpoint

torch.set_num_threads(1)
torch.manual_seed(7301)
out = root / 'experiments/coop-success-distill-v1'
out.mkdir(exist_ok=False)
trace_path = root / 'experiments/coop-v3-prefix-capture-v1/episodes/episode-1273995-50003.json'
parent = root / 'experiments/bc-expansion-v1/all.pt'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
trace = json.loads(trace_path.read_text())
manifest = dict(status='collecting', kind='seen-training-seed successful trajectory retention',
                notGeneralization=True, seed=50003, traceSHA256=sha(trace_path), parentSHA256=sha(parent),
                sourceSHA256={str(p): sha(p) for d in ('rl', 'coop') for p in (root / 'experiment-code' / d).iterdir()
                              if p.is_file() and p.suffix in ('.py', '.mjs', '.json')}, stages=[])


def save():
    temporary = out / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(out / 'manifest.json')


os.environ.update(COOP_POOL='normal', COOP_SPLIT='train', COOP_POOL_SEED='313')
os.environ.pop('COOP_TEAM', None)
environment = f'node {root}/experiment-code/coop/env.mjs --game {root}/game'
save()
try:
    model, checkpoint = load_checkpoint(parent)
    samples = []
    env = Environment(environment)
    try:
        state = env.request('reset', seed=50003, mode='coop', difficulty='ABYSS', budget=128)
        validate_checkpoint(checkpoint, state)
        for entry in trace['actions']:
            assert not state['done'] and state['rounds'] == entry['round']
            index = entry['candidateIndex']
            assert state['candidates'][index] == entry['action']
            samples.append(dict(features=state['features'], action=index))
            state = env.request('step', candidateIndex=index)
        assert state['done'] and state['victory'] is True and len(samples) == 68
        assert state['summary'] == trace['terminal']['summary']
        assert state['partnerAudit'] == trace['terminal']['partnerAudit']
        assert all(state[k] == 0 for k in ('engineErrors', 'actionErrors', 'controllerErrors'))
        (out / 'verified-terminal.json').write_text(json.dumps(state) + '\n')
        with gzip.open(out / 'samples.jsonl.gz', 'wt') as stream:
            for sample in samples:
                stream.write(json.dumps(sample) + '\n')
        manifest.update(status='fitting', samples=68, terminalSHA256=sha(out / 'verified-terminal.json'),
                        sampleSHA256=sha(out / 'samples.jsonl.gz'))
        save()
    finally:
        env.close()
    # Equal, declared finite blocks. No validation or test seed is consulted.
    for stage in range(1, 5):
        metrics, _ = bc_update(model, samples, epochs=100, lr=.0003, batch_size=34)
        model.eval()
        with torch.no_grad():
            distribution, _ = model([s['features'] for s in samples])
            actions = distribution.logits.argmax(-1).tolist()
        accuracy = sum(a == s['action'] for a, s in zip(actions, samples)) / len(samples)
        file = out / f'stage-{stage}.pt'
        save_checkpoint(file, model, checkpoint['schema'], checkpoint['rules'],
                        dict(kind=manifest['kind'], notGeneralization=True, seed=50003,
                             traceSHA256=manifest['traceSHA256'], cumulativeEpochs=stage * 100, metrics=metrics))
        record = dict(stage=stage, cumulativeEpochs=stage * 100, metrics=metrics,
                      demonstrationActionAccuracy=accuracy, checkpointSHA256=sha(file))
        env = Environment(environment)
        try:
            state = env.request('reset', seed=50003, mode='coop', difficulty='ABYSS', budget=128)
            decisions = []
            while not state['done']:
                index, _, _ = model.act(state['features'], greedy=True)
                decisions.append(dict(round=state['rounds'], candidateIndex=index, action=state['candidates'][index]))
                state = env.request('step', candidateIndex=index)
            assert state['summary']['victory'] is state['victory']
            assert all(state[k] == 0 for k in ('engineErrors', 'actionErrors'))
            record.update(greedyVictory=state['victory'], rounds=state['rounds'],
                          controllerErrors=state['controllerErrors'],
                          exactActionSequence=decisions == trace['actions'])
            (out / f'stage-{stage}-eval.json').write_text(json.dumps(dict(checkpointSHA256=sha(file),
                                                                           actions=decisions, terminal=state)) + '\n')
        finally:
            env.close()
        manifest['stages'].append(record)
        save()
        print(json.dumps(record), flush=True)
        if record['greedyVictory']:
            break
    if any(sha(p) != value for p, value in manifest['sourceSHA256'].items()):
        raise RuntimeError('Source changed during retention study')
    manifest['status'] = 'complete'
except Exception as error:
    manifest.update(status='failed', error=repr(error))
    raise
finally:
    save()
