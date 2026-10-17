"""Pinned reuse, OFF-only projection, and fail-closed filesystem identities."""
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
CODE = HERE.parent
BASE = 'd69260ca15ee13b4cf150e1a89ecfd0d66dcb69b'
PARENT = 'experiments/bc-expansion-v1/all.pt'
PARENT_SHA = 'f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a'
MANIFEST_SHA = 'f96bfe2e7b39e35e0de0f26f60f2115c0211ef39b1b20f32d9a0c5c1afcae992'
ARMS = {'steps1000': 5, 'steps3000': 15}
MODELS = ('parent', *ARMS)
CONFIG = dict(epochs=ARMS, drawsPerEpoch=6800, batchSize=34, lr=.0003,
              orderSeed=101, optimizer='Adam', maxGradNorm=.5, expansion='cyclic')
LIMITATIONS = [
    'Six outcome-selected personal wins / 498 labels; label accuracy is not win rate.',
    'Development only: 96 scheduled games share 16 seeds; failures/unrun stay in denominator.',
    'No heldout/sealed/family generalization claim; no selection using development results.',
    'Critic head stays fixed; the shared state encoder and value outputs may change.',
    'Original demo exactness is inherited from reviewed admission; no new real-data validation here.',
]


def safe_path(path):
    """Check lexical ancestors BEFORE any resolve; also rejects dangling symlinks."""
    path = Path(path).absolute()
    # Reject traversal before normalizing so symlink/.. cannot hide an ancestor.
    if '..' in path.parts:
        raise ValueError('Path traversal forbidden: ' + str(path))
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Symlink forbidden: ' + str(item))
    return path


PINS = json.loads(safe_path(HERE / 'pins.json').read_bytes())
if PINS['base'] != BASE:
    raise ValueError('Wrong source base')
for _name, _expected in PINS['files'].items():
    if hashlib.sha256(safe_path(CODE / _name).read_bytes()).hexdigest() != _expected:
        raise ValueError('Pinned dependency changed: ' + _name)
_search = sys.path[:]
sys.path.insert(0, str(CODE / 'gold-study'))
import contracts as G
import data as D
sys.path[:] = _search
C, E, T, N, R, MIB = G.C, G.E, G.T, G.N, G.R, G.MIB
GV = G.load('fit_gold_evidence', CODE / 'gold-study/evaluation.py')


def read_json(path, limit=16 * MIB):
    return G.read_json(safe_path(path), limit)


def identity(path, limit=64 * MIB):
    raw = G.bounded(safe_path(path), limit)
    return dict(sha256=C.digest(raw), bytes=len(raw))


def source_identity(code):
    C.require(safe_path(code) == CODE, 'Run from the deployed code checkout')
    for name, expected in PINS['files'].items():
        C.require(identity(code / name)['sha256'] == expected, 'Source drift: ' + name)
    own = {p.name: identity(p)['sha256'] for p in sorted(safe_path(HERE).iterdir())}
    body = dict(base=BASE, dependencies=PINS['files'], study=own)
    return {**body, 'sha256': C.digest(C.encoded(body))}


class Evidence(G.Evidence):
    def put(self, name, data):
        safe_path(self.out / name)
        return super().put(name, data)

    def verify(self):
        safe_path(self.out)
        for name in self.files:
            safe_path(self.out / name)
        super().verify()


def input_paths(directory):
    """Preserve lexical source paths before reviewed admission resolves them."""
    manifest = read_json(directory / 'manifest.json', 8 * MIB)
    original = safe_path(manifest['manifest']['path'])
    selection = safe_path(manifest['selection']['path'])
    paths = [directory, original, selection, directory / 'source-manifest.json', directory / 'selection.json']
    paths += [original.parent / p for p in read_json(selection, MIB)]
    paths += [directory / r['artifact'] for r in manifest['results']]
    paths += [Path(b['local']) for b in manifest['sources']]
    for path in paths:
        safe_path(path)
    return sorted({str(p.absolute()) for p in paths})


def recheck_inputs(metadata, paths):
    for path in paths:
        safe_path(path)
    for path in metadata['inputFiles']:
        safe_path(path)
    for path in metadata['rawFileSets']:
        safe_path(path)
        for child in Path(path).glob('*.json.gz'):
            safe_path(child)
    G.recheck(metadata)


def project_off(paired, metadata, game):
    schema = metadata['schema']
    old, dims, prefix = G.schema_dimensions(schema)
    C.require(old == game['schema'] and dims == [6410, 419] and prefix == 5267,
              'Frozen OFF dimensions/schema required')
    C.require(len(paired['off']) == len(paired['on']) == len(metadata['labels']) == 498 and
              len(metadata['episodeGroups']) == 6, 'Six groups / 498 labels')
    return [dict(features=G.project(dict(schema=schema, features=s['features'], done=False),
                                    schema, 'off')['features'], action=s['action']) for s in paired['off']]


def model_guard(L, model, ckpt, game):
    N.model_guard(L, model, ckpt, game)
    C.require(ckpt['dimensions'] == [5267, 419, 128], 'Original hidden128 model required')
    C.require(L.torch.get_default_dtype() == L.torch.float32 and
              all(v.device.type == 'cpu' and v.dtype == L.torch.float32 for v in model.state_dict().values()),
              'Original CPU float32 required')


def check_parent(root):
    path = safe_path(root / PARENT)
    N.check_parent(root, path, PARENT_SHA)
    return path


def game_identity(root, code):
    game_path = safe_path(root / 'game')
    rules = read_json(code / 'policy/pins/rules-manifest.json')
    C.require(len(rules['files']) == 486, '486 rule files required')
    for name in rules['files']:
        safe_path(game_path / name)
    pin = R.game_identity(code, game_path)
    actual_path, game = C.game_identity(root, code)  # Static vocabulary, no Match/Battle.
    C.require(actual_path == game_path and C.dimensions(game['schema']) == [5267, 419], 'Static v1 dimensions')
    return game_path, game, pin
