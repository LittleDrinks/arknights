"""CPU ActorCritic weight warm start; no optimizer step or data-order RNG draw."""
from copy import deepcopy

import torch

VERSION = 'shop-gold-v2'


def warm_start(checkpoint, actor_critic, *, generator=None):
    """Return (model, standard_checkpoint). Pass the original rl.learner.ActorCritic.

    Both arms call this with the SAME parent. No mode is encoded in parameters or
    schema. The optional CPU generator and global RNG are preserved, not advanced.
    Optimizer state is discarded: the unmodified bc_update creates a fresh Adam.
    """
    schema = checkpoint['schema']
    if schema.get('version') != 'shop-v1':
        raise ValueError('expected shop-v1 parent, not an already widened checkpoint')
    if not all(isinstance(schema.get(k), list) and all(isinstance(x, str) for x in schema[k])
               for k in ('ids', 'bonds', 'bands')):
        raise ValueError('complete original vocabulary required')
    old_g = 90 + 13 * len(schema['ids']) + 8 * len(schema['bonds']) + len(schema['bands'])
    old_c = 15 + len(schema['ids']) + len(schema['bonds'])
    dimensions = checkpoint['dimensions']
    if list(dimensions[:2]) != [old_g, old_c] or len(dimensions) != 3:
        raise ValueError('parent dimensions disagree with shop-v1 schema')
    state = {k: v.detach().clone() for k, v in checkpoint['model'].items()}
    if any(v.device.type != 'cpu' or v.dtype != torch.float32 for v in state.values()):
        raise ValueError('original CPU float32 ActorCritic checkpoint required')
    weight = state['state.0.weight']
    if list(weight.shape) != [dimensions[2], old_g]:
        raise ValueError('unexpected original first-layer shape')
    added = 3 * len(schema['ids'])
    widened = weight.new_zeros((dimensions[2], old_g + added))
    widened[:, :old_g].copy_(weight)
    state['state.0.weight'] = widened
    # Constructor initialization must not consume the caller's next randperm.
    if generator is not None and generator.device.type != 'cpu':
        raise ValueError('CPU generator required')
    with torch.random.fork_rng(devices=[]):
        if generator is not None:
            torch.set_rng_state(generator.get_state())
        with torch.device('cpu'):
            model = actor_critic(old_g + added, old_c, dimensions[2]).to(device='cpu', dtype=torch.float32)
        model.load_state_dict(state, strict=True)
    result = deepcopy(checkpoint)
    result.update(model=state, dimensions=list(model.dimensions),
                  schema={**deepcopy(schema), 'version': VERSION}, optimizer=None)
    result['metadata'] = {**result.get('metadata', {}), 'goldWarmStart': {
        'parentVersion': 'shop-v1', 'addedColumns': added, 'newWeights': 'zero',
        'optimizer': 'fresh via original bc_update', 'rngConsumed': False}}
    return model, result
