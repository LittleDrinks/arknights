"""Small, fail-closed contracts shared by the two commands. No torch/engine imports."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = '23bdfc3051a5059b41c3ffb7dcb9ddf421b3a697'
RULES = '6ab2468279e926d107788f63d7fe219b85f67b4a'
ARMS = ('primary68', 'survivor68', 'survivor207')
CONFIG = dict(lr=.0003, batchSize=34, epochs=1, draws=6800, updates=200,
              orderSeed=101, selectionSeed=313, optimizer='Adam', maxGradNorm=.5)
TEAMS = {'original': ['original-v1'] * 3,
         'mixed': ['original-v1', 'buy2-v1', 'saver-v1']}
LIMITATIONS = [
    'Single already-seen team victory: a data-source diagnostic, not generalization evidence.',
    'Two survivor seats share one episodeGroup; they are not two independent games.',
    'Survivor selection is outcome-conditioned and has survivorship bias.',
    'Teammate teacher steps assume observed accepted partner shopping calls, not a real installed teacher.',
    'primary68 is a failed learner trajectory control, not positive expert data.',
    'Input interface checks alone do not approve trajectories; retain the independent extractor review separately.',
    'Normal and mixed contain training families; neither is heldout-family evaluation.',
]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def parse(data):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, 'Duplicate JSON key: ' + k)
            result[k] = v
        return result
    def invalid(x):
        raise ValueError('Non-finite JSON: ' + x)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    # Only used inside a newly exclusively created run directory.
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('xb') as f:
        f.write(encoded(value))
    tmp.replace(path)


def new_output(path):
    path = Path(path).absolute()
    require(not path.exists() and not path.is_symlink(), 'Output already exists: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(exist_ok=False)
    return path.resolve()


def relative(root, value):
    p = Path(value)
    return (p if p.is_absolute() else root / p).resolve()


def code_root(root, explicit=None):
    candidates = [relative(root, explicit)] if explicit else [root, root / 'experiment-code']
    found = [p for p in candidates if (p / 'rl/learner.py').is_file() and (p / 'coop/env.mjs').is_file()]
    require(len(found) == 1, 'Provide --experiment-code: expected code at --root or --root/experiment-code')
    return found[0]


def source_identity(code):
    pins = parse((HERE / 'source-pins.json').read_bytes())
    require(pins['base'] == BASE, 'Source pin base mismatch')
    for name, expected in pins['files'].items():
        require(sha(code / name) == expected, 'Source guard: ' + name)
    own = {p.name: sha(p) for p in sorted(HERE.iterdir()) if p.suffix in ('.py', '.mjs', '.json')}
    result = {'base': BASE, 'originalFiles': pins['files'], 'studyFiles': own}
    return {**result, 'SHA256': digest(encoded(result))}


def clean_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('COOP_', 'STRONGHOLD_', 'PYTHON', 'NODE_', 'LD_', 'DYLD_'))}
    env.update(OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', CUDA_VISIBLE_DEVICES='')
    return env


def game_identity(root, code):
    candidates = [p for p in (root / 'game', root / 'Stronghold-Protocol') if (p / 'server/match/Match.js').exists()]
    # Local research and the authorized server use different checkout directory names.
    require(len(candidates) == 1, 'Exactly one game or Stronghold-Protocol checkout required')
    game = candidates[0]
    pin = parse((code / 'policy/pins/rules-manifest.json').read_bytes())
    require(pin['rulesCommit'] == RULES, 'Rules manifest revision')
    files = {str(p.relative_to(game)) for folder in ('server', 'shared', 'data')
             for p in (game / folder).rglob('*') if p.is_file() and p.suffix in ('.js', '.mjs', '.json')}
    files.update(('package.json', 'package-lock.json'))
    require(files == set(pin['files']), 'Game source file set differs')
    for name in sorted(files):
        require(sha(game / name) == pin['files'][name], 'Game source guard: ' + name)
    head = subprocess.check_output(['git', '-C', str(game), 'rev-parse', 'HEAD'], timeout=5, text=True).strip()
    require(head == RULES, 'Game HEAD differs')
    result = subprocess.run(['node', str(HERE / 'static.mjs'), str(game), str(code)],
                            env=clean_env(), capture_output=True, timeout=10, check=True)
    require(len(result.stdout) < 1_000_000 and not result.stderr, 'Static loader output')
    data = parse(result.stdout)
    return game, {**data, 'rules': RULES, 'treeSHA256': pin['treeSha256']}


def learner(code):
    os.environ.update(OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', CUDA_VISIBLE_DEVICES='')
    spec = importlib.util.spec_from_file_location('survivor_original_learner', code / 'rl/learner.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dimensions(schema):
    require(set(schema) == {'version', 'ids', 'bonds', 'bands'} and schema['version'] == 'shop-v1', 'Schema keys/version')
    for key in ('ids', 'bonds', 'bands'):
        v = schema[key]
        require(isinstance(v, list) and all(type(x) is str for x in v) and v == sorted(set(v)), 'Schema vocabulary')
    return [90 + 8 * len(schema['bonds']) + len(schema['bands']) + 13 * len(schema['ids']),
            15 + len(schema['ids']) + len(schema['bonds'])]


def features(f, dims, terminal=False):
    require(set(f) == {'global', 'candidates', 'mask'}, 'Feature keys (no metadata/future features)')
    cs, mask = f['candidates'], f['mask']
    require(type(cs) is list and (len(cs) == 0 if terminal else 0 < len(cs) <= 4096), 'Candidate count')
    require(type(mask) is list and len(mask) == len(cs) and all(type(x) is bool for x in mask), 'Mask shape/type')
    require(terminal or any(mask), 'No legal candidate')
    for v, n in [(f['global'], dims[0])] + [(c, dims[1]) for c in cs]:
        require(type(v) is list and len(v) == n, 'Feature dimensions')
        require(all(type(x) in (int, float) and math.isfinite(x) and abs(x) <= 1e6 for x in v), 'Feature scalar')


def sample_contract(s, dims):
    require(set(s) == {'features', 'action'}, 'Sample keys (no source/terminal metadata)')
    features(s['features'], dims)
    a = s['action']
    require(type(a) is int and 0 <= a < len(s['features']['mask']) and s['features']['mask'][a], 'Illegal BC action')


def terminal_contract(t, *, seed, steps, schema, teacher=False):
    require(t['done'] is True and t['schema'] == schema and t['rules'] == RULES, 'Terminal/schema/rules')
    require(t['steps'] == steps and type(t['steps']) is int, 'Terminal steps')
    e, s = t['episode'], t['summary']
    require(e['seed'] == seed and e['mode'] == 'coop' and e['difficulty'] == 'ABYSS' and e['budget'] == 128, 'Episode identity')
    require(s['seed'] == seed and s['modeId'] == 'mode_multi_abyss' and s['difficulty'] == 'ABYSS', 'Summary identity')
    require(type(s['victory']) is bool and t['victory'] is s['victory'] and s['errors'] == 0, 'Summary outcome/errors')
    require(type(s['roundsPassed']) is int and s['roundsPassed'] > 0 and t['rounds'] == s['roundsPassed'] and
            type(s['reason']) is str and bool(s['reason']), 'Incomplete terminal summary')
    require(t['reward'] == (1 if s['victory'] else -1), 'Terminal reward')
    for k in ('engineErrors', 'actionErrors'):
        require(type(t[k]) is int and t[k] == 0, 'Terminal ' + k)
    require(t.get('fatalRejections', 0) == 0, 'Fatal rejection')  # Pinned CoopEnv.assertHealthy also latches fatal.
    require(type(teacher) is bool, 'Explicit terminal collection mode')
    require(type(t['controllerErrors']) is int and t['controllerErrors'] >= 0 and
            type(t['controllerDiagnosticErrors']) is int and
            t['controllerErrors'] == t['controllerDiagnosticErrors'] and
            (t['controllerErrors'] == 0 if teacher else t['controllerErrors'] == len(t['rejectedActions'])),
            'Controller rejection accounting')
    audits = t['partnerAudit']
    require(set(audits) == {'partner-1', 'partner-2', 'partner-3'}, 'Partner audit seats')
    rejects = list(t['rejectedActions'])
    for a in audits.values():
        require(type(a['rejected']) is int and a['rejected'] == len(a['rejections']), 'Partner rejection count')
        rejects.extend(a['rejections'])
    require(t['partnerActionErrors'] == sum(a['rejected'] for a in audits.values()), 'Partner rejection total')
    # Native validation sometimes returns {error: CODE} with no `ok` property.
    # Keep these actual rejections and all counters; accept only the already guarded
    # pure-validation shape, never an explicit success or a missing error reason.
    require(all(r['diagnostic'] is True and r['reason'] == 'pure-validation' and
                isinstance(r.get('result'), dict) and
                ('ok' not in r['result'] or r['result']['ok'] is False) and
                isinstance(r['result'].get('error'), str) and bool(r['result']['error']) and
                not r.get('pathdiff') for r in rejects), 'Non-diagnostic rejection')
    diagnostics = e['diagnostics']
    for k in ('partnerAudit', 'partnerActionErrors', 'controllerDiagnosticErrors', 'rejectedActions',
              'controllerBlockedMoves', 'controllerBlockedMoveReasons', 'filteredMoves'):
        require(diagnostics[k] == t[k], 'Terminal diagnostics mismatch: ' + k)
    ps = s['players']
    require(len(ps) == 4 and {(p['playerId'], p['seat']) for p in ps} ==
            {('learner', 0), ('partner-1', 1), ('partner-2', 2), ('partner-3', 3)}, 'Terminal four-seat identity')
    for p in ps:
        require(type(p['victory']) is bool and type(p['alive']) is bool and type(p['roundsPassed']) is int and
                0 <= p['roundsPassed'] <= s['roundsPassed'] and
                (p['eliminatedRound'] is None or type(p['eliminatedRound']) is int), 'Individual terminal outcome')
        require(all(k in p for k in ('lineup', 'bonds', 'stats')), 'Missing full player terminal')
        require(p['victory'] is (s['victory'] and p['alive']), 'Individual/team survival mismatch')
        require((p['alive'] and p['eliminatedRound'] is None and p['roundsPassed'] == s['roundsPassed']) or
                (not p['alive'] and type(p['eliminatedRound']) is int and p['eliminatedRound'] >= 1 and
                 p['roundsPassed'] == max(0, p['eliminatedRound'] - 1)), 'Elimination/rounds mismatch')
    require(t['observation']['public']['phase'] == 'RESULT' and t['observation']['own']['playerId'] == 'learner'
            and t['observation']['own']['seat'] == 0 and t['candidates'] == [], 'Terminal observation/seat')
    features(t['features'], dimensions(schema), terminal=True)
    primary = next(p for p in ps if p['seat'] == 0)
    require(t['observation']['own'].get('alive') is primary['alive'], 'Primary observation/summary alive mismatch')
    require(t['observation']['own'].get('lp') == primary.get('lp') and
            type(primary.get('lp')) in (int, float) and
            type(t['observation']['own'].get('lp')) in (int, float), 'Primary observation/summary LP mismatch')
    return primary
