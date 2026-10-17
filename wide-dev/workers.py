"""Spawn-only persistent inference workers; original modules are never patched."""
import os
from pathlib import Path
import signal
import time

from contracts_wide import C, F, P, CODE, MODELS, digest, require, safe, source_identity
from admission_wide import model_identity, proof_identity
from evaluation import episode_seal, verify_job

# Private per-process state, populated by ProcessPoolExecutor.initializer once.
_state = None


def initialize(config):
    global _state
    require(_state is None, 'Worker must initialize exactly once')
    os.setsid()  # Separate worker group; P.Protocol owns a separate group per Node.
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
    require(sorted(os.sched_getaffinity(0)) == config['affinity'] and len(config['affinity']) <= 16,
            'Worker must inherit the bounded CPU affinity')
    require(source_identity(CODE) == config['source'], 'Worker actual source/HEAD mismatch')
    L = C.learner(CODE)  # Original torch thread1 setup, exactly once in this process.
    require(L.torch.get_num_threads() == L.torch.get_num_interop_threads() == 1, 'Worker torch thread1')
    models = {}
    for name in MODELS:
        path = safe(config['paths'][name])
        require(digest(path) == config['files'][name], 'Worker checkpoint SHA/bytes before load')
        model, ckpt = L.load_checkpoint(path)
        model.eval()
        model.requires_grad_(False)
        require(model_identity(L, model, ckpt, config['game']) == config['models'][name], 'Worker loaded model mismatch')
        require(digest(path) == config['files'][name], 'Worker checkpoint changed during load')
        models[name] = (model, ckpt)
    _state = (config, L, models)


def perform_job(row, deadline, byte_limit, pool):
    require(_state is not None, 'Worker admission required')
    config, L, models = _state
    model, checkpoint = models[row['model']]
    require(time.monotonic() < deadline, 'Job deadline before inference')
    before = model_identity(L, model, checkpoint, config['game'])
    require(before == config['models'][row['model']], 'Actual inference model drift before job')
    require(L.torch.get_num_threads() == L.torch.get_num_interop_threads() == 1 and
            sorted(os.sched_getaffinity(0)) == config['affinity'], 'Worker resources drift')
    # The actual P entrypoint and its original default Protocol. Arbitrary model
    # name steps3000 is consumed without an alias by the original state contract.
    result, files = P.game_job(row, model, checkpoint, Path(config['gamePath']), Path(config['out']),
                               deadline, byte_limit - 32768, pool)
    after = model_identity(L, model, checkpoint, config['game'])
    require(after == before == config['models'][row['model']], 'Actual inference model drift after job')
    require(L.torch.get_num_threads() == L.torch.get_num_interop_threads() == 1 and
            sorted(os.sched_getaffinity(0)) == config['affinity'], 'Worker resources drift after job')
    # Protocol timeout is a sealed technical failure. Its killpg cleanup and
    # proof hashing get up to 3s, always inside the absolute batch deadline.
    seal_deadline = min(config['deadline'], deadline + 3)
    seal = episode_seal(Path(config['out']) / row['id'], seal_deadline)
    require(seal['files'] == files, 'Original P publication mismatch')
    verify_job(result, Path(config['out']), seal, byte_limit - 32768, seal_deadline)
    seal['workerProof'] = dict(kind='wide-dev/inference-proof-v1', job=row['id'], model=row['model'], pid=os.getpid(),
        before=proof_identity(before), after=proof_identity(after), frozen=True, intraThreads=1, interThreads=1,
        affinity=sorted(os.sched_getaffinity(0)), checkpoint=config['files'][row['model']],
        sourceSHA256=C.digest(C.encoded(config['source'])))
    return result, seal
