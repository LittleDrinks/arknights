"""Pinned reuse; projection belongs to validation, never the actor or raw writer."""
import importlib.util
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CODE = HERE.parent
PINS = json.loads((HERE / 'pins.json').read_bytes())
import hashlib
for name, expected in PINS['files'].items():
    if hashlib.sha256((CODE / name).read_bytes()).hexdigest() != expected:
        raise ValueError('Pinned dependency changed: ' + name)
_search_path = sys.path[:]
sys.path.insert(0, str(CODE / 'natural-win-bc'))
import support as N
import admission as A
from support import C, E, T, MIB, bounded, read_json, pool_sha
sys.path[:] = _search_path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load('gold_recapture', CODE / 'gold-features/recapture.py')
MODES = {'warmBaseline': 'off', 'trainedOff': 'off', 'trainedOn': 'on'}
CONFIG = dict(lr=.0003, batchSize=34, epochs=1, draws=6800, updates=200,
              orderSeed=101, optimizer='Adam', maxGradNorm=.5, expansion='cyclic')
REVIEW = N.REVIEW
LIMITATIONS = [
    'Source semantics remain pending independent acceptance by main; hashes are not approval.',
    'Six outcome-selected natural personal wins; training loss/accuracy is not win rate.',
    '96 scheduled games share 16 seed groups across arms/pools; not 96 independent seeds.',
    'Development only: no heldout, validation or sealed seeds; no generalization claim.',
    'Warm outputs are numerically equivalent within tolerance, not a bitwise v1 claim.',
]


class Evidence(N.Evidence):
    def __init__(self, out, limit=512 * MIB):
        super().__init__(out, limit)
        self.files = {}

    def put(self, name, data):
        result = super().put(name, data)
        self.files[name] = result
        return result

    def verify(self):
        C.require({p.name for p in self.out.iterdir()} == set(self.files), 'Published evidence file set drift')
        for name, identity in self.files.items():
            raw = bounded(self.out / name, 64 * MIB)
            C.require(len(raw) == identity['bytes'] and C.digest(raw) == identity['sha256'],
                      'Published evidence drift: ' + name)


def source_identity(code):
    C.require(code == CODE, 'Run the CLI from the configured experiment-code checkout')
    for name, expected in PINS['files'].items():
        C.require(C.sha(code / name) == expected, 'Source guard: ' + name)
    own = {p.name: C.sha(p) for p in sorted(HERE.iterdir()) if p.is_file()}
    body = dict(base=PINS['base'], dependencies=PINS['files'], study=own)
    return {**body, 'sha256': C.digest(C.encoded(body))}


def schema_dimensions(schema):
    C.require(schema.get('version') == 'shop-gold-v2', 'Expected actual shop-gold-v2 schema')
    old = {**schema, 'version': 'shop-v1'}
    g, c = C.dimensions(old)
    return old, [g + 3 * len(schema['ids']), c], g


def project(state, schema, mode):
    C.require(mode in ('off', 'on'), 'Unknown gold mode')
    C.require(state['schema'] == schema, 'Actual schema differs from checkpoint')
    old, dims, prefix = schema_dimensions(state['schema'])
    C.features(state['features'], dims, terminal=state['done'])
    tail = state['features']['global'][prefix:]
    C.require(all(math.isfinite(x) and x >= 0 for x in tail), 'Invalid gold tail')
    C.require(mode != 'off' or all(x == 0 for x in tail), 'OFF gold tail must be zero')
    return {**state, 'schema': old,
            'features': {**state['features'], 'global': state['features']['global'][:prefix]}}


def model_guard(L, model, ckpt, schema):
    _, dims, _ = schema_dimensions(schema)
    C.require(ckpt['schema'] == schema and ckpt['rules'] == C.RULES and
              ckpt['dimensions'][:2] == dims, 'Model schema/rules/dimensions')
    C.require(L.torch.get_default_dtype() == L.torch.float32, 'Original forward requires default float32')
    C.require(all(v.device.type == 'cpu' and v.dtype == L.torch.float32 and
                  L.torch.isfinite(v).all().item() for v in model.state_dict().values()), 'CPU finite float32 model')


def environment(code, mode, pool):
    C.require(mode in ('off', 'on') and pool in C.TEAMS, 'Unknown gold mode/pool')
    env = {k: v for k, v in C.clean_env().items() if not k.startswith(('GOLD_FEATURES_', 'ARK_'))}
    env.update(GOLD_FEATURES_ROOT=str(code), GOLD_FEATURES_MODE=mode,
               COOP_POOL='normal' if pool == 'original' else 'mixed', COOP_SPLIT='eval',
               COOP_POOL_SEED='313', COOP_TEAM=','.join(C.TEAMS[pool]))
    return env


def recheck(metadata):
    for path, expected in metadata['inputFiles'].items():
        C.require(C.digest(bounded(path, 64 * MIB)) == expected, 'Input drift: ' + path)
    for directory, names in metadata['rawFileSets'].items():
        C.require(sorted(p.name for p in Path(directory).glob('*.json.gz')) == names, 'Raw file set drift')
