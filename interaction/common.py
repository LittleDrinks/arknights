"""Artifact and feature contracts; no policy has access to episode metadata."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'interaction'
RULES = '6ab2468279e926d107788f63d7fe219b85f67b4a'
BASE = '654652785fec4c7c8e9fe3522875bef702183799'
DEFAULT_GAME = ROOT.parent / 'Stronghold-Protocol'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f'Non-finite JSON: {value}')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def write_json(path, value):
    Path(path).write_bytes(json_bytes(value))


def source_identity():
    paths = sorted(HERE.glob('*.py')) + sorted((ROOT / 'rl').glob('*.mjs')) + [ROOT / 'rl/learner.py']
    files = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    return {'baseCommit': BASE,
            'headCommit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
            'files': files, 'sourceSHA256': hashlib.sha256(json_bytes(files)).hexdigest()}


def schema_dimensions(schema):
    require(isinstance(schema, dict) and set(schema) == {'version', 'ids', 'bonds', 'bands'}, 'Schema keys')
    require(schema['version'] == 'shop-v1', 'Unsupported feature schema')
    for key in ('ids', 'bonds', 'bands'):
        values = schema[key]
        require(isinstance(values, list) and all(isinstance(x, str) for x in values), 'Schema vocabulary')
        require(values == sorted(set(values)), 'Schema vocabulary must be unique and sorted')
    return [90 + 8 * len(schema['bonds']) + len(schema['bands']) + 13 * len(schema['ids']),
            15 + len(schema['ids']) + len(schema['bonds'])]


def game_schema(game):
    game = Path(game).resolve()
    head = subprocess.check_output(['git', '-C', str(game), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == RULES, 'Game rule revision mismatch')
    dirty = subprocess.check_output(['git', '-C', str(game), 'status', '--porcelain', '--untracked-files=no',
                                     '--', 'server', 'shared', 'data'], text=True)
    require(not dirty.strip(), 'Modified game rules/data')
    data = {k: strict_json((game / 'data' / (k + '.json')).read_text())
            for k in ('chess', 'items', 'bands', 'bonds')}
    schema = {'version': 'shop-v1', 'ids': sorted(list(data['chess']) + list(data['items'])),
              'bonds': sorted(data['bonds']), 'bands': sorted(data['bands'])}
    schema_dimensions(schema)
    return schema


def validate_features(features, dimensions, *, terminal=False):
    require(isinstance(features, dict) and set(features) == {'global', 'candidates', 'mask'}, 'Feature keys')
    g, candidates, mask = (features[k] for k in ('global', 'candidates', 'mask'))
    require(isinstance(candidates, list) and
            (len(candidates) == 0 if terminal else 0 < len(candidates) <= 4096), 'Candidate count')
    require(isinstance(mask, list) and len(mask) == len(candidates), 'Mask shape')
    require(all(type(v) is bool for v in mask) and (terminal or any(mask)), 'Mask must contain a legal bool')
    for vec, size in [(g, dimensions[0])] + [(v, dimensions[1]) for v in candidates]:
        require(isinstance(vec, list) and len(vec) == size, 'Feature dimension mismatch')
        require(all(type(x) in (int, float) and math.isfinite(x) and abs(x) <= 1e6 for x in vec),
                'Features must be finite bounded numbers')


def validate_label(sample, dimensions):
    validate_features(sample['features'], dimensions)
    action = sample['action']
    mask = sample['features']['mask']
    require(type(action) is int and 0 <= action < len(mask) and mask[action], 'Illegal current candidate label')


def output_dir(path):
    path = Path(path).resolve()
    require(path.is_relative_to(HERE), 'Write artifacts only under interaction/')
    path.mkdir(parents=True, exist_ok=False)
    return path
