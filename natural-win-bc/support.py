"""Pinned reuse of the reviewed study; no changes to its modules or globals."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CODE = HERE.parent
PINS = json.loads((HERE / 'pins.json').read_bytes())
for _name, _sha in PINS['files'].items():
    if hashlib.sha256((CODE / _name).read_bytes()).hexdigest() != _sha:
        raise ValueError('Dependency changed: ' + _name)
sys.path.insert(0, str(CODE / 'survivor-study'))
import common as C


def dependency(name):
    spec = importlib.util.spec_from_file_location('natural_reuse_' + name, CODE / 'survivor-study' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T = dependency('train')
E = dependency('evaluate')
MIB = 1024 ** 2
REVIEW = 'collector-source-trust-pending-main-independent-acceptance'
LIMITATIONS = [
    'Hash/interface checks do not approve collector semantics; main must independently accept the source.',
    'Positive-only outcome-conditioned BC; negative and failed episodes remain audit evidence.',
    'normal/mixed with the same seed share one episodeGroup, not independent episodes.',
    'Training-label accuracy is not win rate; no heldout families or sealed seeds are evaluated.',
]
SELECTOR = 'all labels of complete team wins with learner alive, individual victory, 14 rounds, rejectedLabels=0'


def bounded(path, limit):
    path = Path(path)
    C.require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit, 'Input file/size: ' + str(path))
    with path.open('rb') as f:
        data = f.read(limit + 1)
    C.require(len(data) <= limit, 'Input grew past limit')
    return data


def read_json(path, limit=16 * MIB):
    return C.parse(bounded(path, limit))


def source_identity(code):
    for name, expected in PINS['files'].items():
        C.require(C.sha(code / name) == expected, 'Source guard: ' + name)
    own = {p.name: C.sha(p) for p in sorted(HERE.iterdir()) if p.is_file()}
    result = dict(base=PINS['base'], dependencies=PINS['files'], module=own, collector=PINS['collector'])
    return {**result, 'SHA256': C.digest(C.encoded(result))}


def game_identity(root, code):
    pin = read_json(code / 'policy/pins/rules-manifest.json')
    C.require(len(pin['files']) == 486, 'Expected 486 pinned game source files')
    return C.game_identity(root, code)


def pool_sha(code):
    return C.digest(json.dumps(read_json(code / 'coop/pool.json'), separators=(',', ':'), ensure_ascii=False).encode())


def capture_sources(manifest):
    sources = manifest['sourceSHA256']
    expected = {k: v for k, v in PINS['files'].items() if k.startswith(('rl/', 'coop/'))}
    found, collectors = {}, []
    prefixes = set()
    for path, identity in sources.items():
        C.require(Path(path).is_absolute(), 'Capture source absolute path')
        if Path(path).name == PINS['collector']['name']:
            collectors.append(identity)
            continue
        matches = [k for k in expected if path.endswith('/' + k)]
        C.require(len(matches) == 1 and matches[0] not in found, 'Unexpected/duplicate capture source')
        name = matches[0]
        prefixes.add(path[:-len(name)])
        found[name] = identity
    C.require(found == expected and len(prefixes) == 1 and collectors == [PINS['collector']['sha256']],
              'Capture source/JSON/collector SHA mismatch')


class Evidence:
    """Train evidence has a finite aggregate cap; never overwrite a published file."""
    def __init__(self, out, limit=512 * MIB):
        self.out, self.limit, self.used = out, limit, 0

    def put(self, name, data):
        C.require(self.used + len(data) <= self.limit, 'Evidence output budget')
        with (self.out / name).open('xb') as f:
            f.write(data)
        self.used += len(data)
        return {'sha256': C.digest(data), 'bytes': len(data)}

    def json(self, name, value, compress=False):
        data = C.encoded(value)
        C.require(len(data) <= 64 * MIB, 'Evidence record size')
        return self.put(name, gzip.compress(data, mtime=0) if compress else data)


def check_parent(root, path, expected):
    C.require(path == (root / 'experiments/bc-expansion-v1/all.pt').resolve(), 'Designated parent path required')
    C.require(len(expected) == 64 and C.digest(bounded(path, 32 * MIB)) == expected, 'Designated parent SHA mismatch')


def model_guard(L, model, ckpt, game):
    C.require(ckpt['schema'] == game['schema'] and ckpt['rules'] == C.RULES and
              ckpt['dimensions'][:2] == C.dimensions(game['schema']) and ckpt['dimensions'][2] == 64,
              'Original ActorCritic dimensions/schema/rules')
    C.require(all(L.torch.isfinite(v).all().item() for v in model.state_dict().values()), 'Nonfinite model')
