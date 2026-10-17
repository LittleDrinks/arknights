"""Future server-only recapture entrypoint. Importing this module runs no game."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import time

VERSION = 'shop-gold-v2'
RULES = '6ab2468279e926d107788f63d7fe219b85f67b4a'
TEAMS = {'normal': ['original-v1'] * 3, 'mixed': ['original-v1', 'buy2-v1', 'saver-v1']}
HERE = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_bounded(path, limit):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('input exceeds byte limit')
    return raw


def parse(raw):
    def invalid(_):
        raise ValueError('non-finite JSON')
    return json.loads(raw, parse_constant=invalid)


def unpack(path, limit):
    packed = read_bounded(path, limit)
    # Limit decompressed size as well, including gzip bombs.
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('decompressed demo exceeds byte limit')
    return packed, parse(raw)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same(old, new, path='$'):
    """JSON field equality, with bounded first-difference evidence (never dump data)."""
    if isinstance(old, dict) and isinstance(new, dict):
        require(old.keys() == new.keys(), f'field keys differ at {path[:240]}')
        for key in old:
            same(old[key], new[key], f'{path}.{key}')
    elif isinstance(old, list) and isinstance(new, list):
        require(len(old) == len(new), f'length differs at {path[:240]}')
        for i, (a, b) in enumerate(zip(old, new)):
            same(a, b, f'{path}[{i}]')
    else:
        # JS JSON can serialize 1.0 as 1; booleans are never numbers here.
        numeric = type(old) in (int, float) and type(new) in (int, float)
        require((numeric or type(old) is type(new)) and old == new,
                f'value differs at {path[:240]}')


def dimensions(schema):
    return (90 + 13 * len(schema['ids']) + 8 * len(schema['bonds']) + len(schema['bands']),
            15 + len(schema['ids']) + len(schema['bonds']))


def positive(demo, row):
    require(row['status'] == 'complete' and row['eligiblePositive'] is True,
            'selection must contain admitted real positive rows')
    t = demo['terminal']
    episode, summary = t['episode'], t['summary']
    require(demo['ok'] is True and t['done'] is True and t['victory'] is True and
            summary['victory'] is True and demo['rejectedLabels'] == 0 and demo['samples'],
            'not a complete positive demo')
    same({'seed': row['seed'], 'mode': 'coop', 'difficulty': 'ABYSS', 'budget': 128},
         {k: episode[k] for k in ('seed', 'mode', 'difficulty', 'budget')})
    require(type(row['seed']) is int and 30001 <= row['seed'] <= 31024 and row['pool'] in TEAMS,
            'outside frozen naturalteacher training search')
    require(summary['seed'] == row['seed'] and summary['modeId'] == 'mode_multi_abyss' and
            summary['errors'] == 0 and t['rounds'] == summary['roundsPassed'] and
            len(summary['players']) == 4, 'terminal summary identity mismatch')
    learner = [p for p in summary['players'] if p['playerId'] == 'learner']
    require(len(learner) == 1 and learner[0]['victory'] is True and
            learner[0]['alive'] is True and learner[0]['roundsPassed'] == 14,
            'learner did not survive the final round')
    require(demo['rules'] == t['rules'] == RULES and t['engineErrors'] == t['actionErrors'] == 0 and
            t['controllerErrors'] == t['controllerDiagnosticErrors'], 'rules/errors mismatch')
    partners = episode['partners']
    same([partners[k] for k in ('pool', 'split', 'poolSeed', 'fixed')],
         [row['pool'], 'train', 313, TEAMS[row['pool']]])
    same([row[k] for k in ('teamVictory', 'learnerVictory', 'learnerAlive', 'rounds',
                          'learnerRounds', 'rejectedLabels', 'samples')],
         [True, True, True, t['rounds'], 14, 0, len(demo['samples'])])
    require(demo['schema']['version'] == 'shop-v1', 'expected original demo schema')
    same(demo['schema'], t['schema'])
    g, c = dimensions(demo['schema'])
    for sample in demo['samples']:
        f, a = sample['features'], sample['action']
        require(len(f['global']) == g and len(f['candidates']) == len(f['mask']) and
                all(len(x) == c for x in f['candidates']) and type(a) is int and
                0 <= a < len(f['mask']) and f['mask'][a] is True, 'invalid old label/dimensions')


def verify_recapture(old, new, mode):
    """Compare the WHOLE response, including all sample fields and terminal fields."""
    require(mode in ('off', 'on'), 'invalid mode')
    expected = {**old['schema'], 'version': VERSION}
    same(expected, new['schema'], '$.schema')
    same(expected, new['terminal']['schema'], '$.terminal.schema')
    require(len(new['samples']) == len(old['samples']), 'sample count mismatch')
    normalized = deepcopy(new)
    normalized['schema'] = deepcopy(old['schema'])
    normalized['terminal']['schema'] = deepcopy(old['terminal']['schema'])
    pairs = [(old['terminal']['features'], normalized['terminal']['features'])]
    pairs += [(a['features'], b['features']) for a, b in zip(old['samples'], normalized['samples'])]
    added = 3 * len(old['schema']['ids'])
    g, _ = dimensions(old['schema'])
    for previous, current in pairs:
        require(len(previous['global']) == g and len(current['global']) == g + added,
                'global dimensions mismatch')
        tail = current['global'][g:]
        require(all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in tail),
                'invalid golden count encoding')
        require(mode != 'off' or all(x == 0 for x in tail), 'OFF has nonzero gold')
        current['global'] = current['global'][:g]
    same(old, normalized)


def source_bindings(manifest, root, collector):
    bindings = []
    for original, identity in manifest['sourceSHA256'].items():
        p = Path(original)
        if p.parent.name in ('rl', 'coop'):
            local = root / p.parent.name / p.name
        elif p.name == 'collect-natural-coop-teachers.py':
            local = collector
        else:
            raise ValueError('unrecognized frozen source path')
        require(sha(read_bounded(local, 5_000_000)) == identity, 'frozen source SHA256 mismatch')
        bindings.append(dict(original=original, local=str(local), sha256=identity))
    expected = {str(p.resolve()) for d in ('rl', 'coop') for p in (root / d).iterdir()
                if p.suffix in ('.py', '.mjs', '.json') and p.is_file()} | {str(collector)}
    require({b['local'] for b in bindings} == expected and len(bindings) == len(expected),
            'incomplete or duplicate frozen source coverage')
    return bindings


def capture(command, environment, request, deadline, limit):
    """One original CoopEnv CLI process, one demo, then EOF/close. No shell."""
    if time.monotonic() >= deadline:
        raise TimeoutError('recapture total time limit before child start')
    process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        process.stdin.write((json.dumps(request) + '\n').encode())
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('recapture total time limit')
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    chunks[key.data].extend(chunk)
                    require(len(chunks['stdout']) <= limit and len(chunks['stderr']) <= 16384,
                            'child output exceeds bound')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('recapture total time limit')
        code = process.wait(timeout=remaining)
        require(code == 0, 'recapture child failed: ' + chunks['stderr'].decode(errors='replace')[:1000])
        raw = bytes(chunks['stdout'])
        require(raw.endswith(b'\n') and len(raw.splitlines()) == 1, 'expected one full raw demo response')
        return raw
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()
        if not process.stdin.closed:
            process.stdin.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='experiment-code checkout')
    parser.add_argument('--game', type=Path, required=True, help='local pinned Stronghold checkout')
    parser.add_argument('--collector', type=Path, required=True, help='original collection script for SHA verification')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--selection', type=Path, required=True, help='JSON list of real .json.gz paths; relative to manifest')
    parser.add_argument('--out', type=Path, required=True, help='new, nonexistent directory')
    parser.add_argument('--node', default='node', help='local Node 24 executable')
    parser.add_argument('--seconds', type=float, default=120, help='total wall time including admission; max 1800')
    parser.add_argument('--max-bytes', type=int, default=5_000_000, help='per-input/decompressed/child bound; max 64 MiB')
    parser.add_argument('--output-bytes', type=int, default=5_000_000, help='total published bytes; max 256 MiB')
    args = parser.parse_args(argv)
    require(math.isfinite(args.seconds) and 0 < args.seconds <= 1800 and
            1024 <= args.max_bytes <= 64 * 1024**2 and 4096 <= args.output_bytes <= 256 * 1024**2,
            'invalid resource bounds')
    deadline = time.monotonic() + args.seconds
    root, collector = args.root.resolve(), args.collector.resolve()
    manifest_path, selection_path = args.manifest.resolve(), args.selection.resolve()
    manifest_raw = read_bounded(manifest_path, args.max_bytes)
    require(sha(manifest_raw) == args.manifest_sha256, 'frozen manifest SHA256 mismatch')
    manifest = parse(manifest_raw)
    require(manifest['status'] in ('complete', 'incomplete', 'failed') and manifest.get('finishedAt') and
            manifest['planned'] == 2048 and
            manifest['pairedTrainingSeeds'] == 1024 and len(manifest['games']) == 2048 and
            {(r['seed'], r['pool']) for r in manifest['games']} ==
            {(seed, pool) for seed in range(30001, 31025) for pool in TEAMS},
            'expected finalized frozen naturalteacher manifest')
    bindings = source_bindings(manifest, root, collector)
    selection_raw = read_bounded(selection_path, args.max_bytes)
    selected = parse(selection_raw)
    require(isinstance(selected, list) and 0 < len(selected) <= 32 and
            all(isinstance(p, str) and p.endswith('.json.gz') for p in selected),
            'select 1..32 explicit positive gzip paths')
    paths = [(manifest_path.parent / p).resolve() for p in selected]
    require(len(set(paths)) == len(paths), 'duplicate selection')
    plan = []
    for path in paths:
        require(time.monotonic() < deadline, 'recapture total time limit during admission')
        rows = [r for r in manifest['games'] if 'artifact' in r and
                (manifest_path.parent / r['artifact']).resolve() == path]
        require(len(rows) == 1, 'selected artifact is not uniquely bound by manifest')
        packed, old = unpack(path, args.max_bytes)
        row = rows[0]
        require(sha(packed) == row['artifactSHA256'] and len(packed) == row['bytes'], 'old gzip SHA/size mismatch')
        positive(old, row)
        plan.append((path, row))
    # Refuse alternate loader/controller injection; only our loader is installed.
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('COOP_', 'ARK_', 'GOLD_FEATURES_', 'NODE_'))}
    command = [args.node, '--max-old-space-size=768', '--loader', str(HERE / 'loader.mjs'),
               str(root / 'coop/env.mjs'), '--game', str(args.game.resolve())]
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    written = 0

    def publish(name, raw, reserve=0):
        nonlocal written
        require(written + len(raw) + reserve <= args.output_bytes, 'total output byte limit')
        with (output / name).open('xb') as stream:
            stream.write(raw)
        written += len(raw)

    clock = lambda: datetime.now(timezone.utc).isoformat()
    record = dict(status='running', kind='paired obs-only naturalteacher recapture, not evaluation',
                  startedAt=clock(), manifest=dict(path=str(manifest_path), sha256=sha(manifest_raw)),
                  selection=dict(path=str(selection_path), sha256=sha(selection_raw)), sources=bindings,
                  implementation={p.name: sha(p.read_bytes()) for p in HERE.iterdir()
                                  if p.suffix in ('.mjs', '.py')},
                  command=command, game=str(args.game.resolve()),
                  fixedConfig=dict(split='train', poolSeed=313, teams=TEAMS, difficulty='ABYSS', budget=128),
                  limits=dict(seconds=args.seconds, perInputBytes=args.max_bytes, outputBytes=args.output_bytes),
                  results=[])
    # Reserve enough space to always retain a small failed/complete provenance record.
    reserve = len(json.dumps(record).encode()) + len(plan) * 3000 + 4096
    try:
        require(written + reserve <= args.output_bytes, 'output budget too small for provenance')
        publish('source-manifest.json', manifest_raw, reserve)
        publish('selection.json', selection_raw, reserve)
        for path, row in plan:
            packed, old = unpack(path, args.max_bytes)
            require(sha(packed) == row['artifactSHA256'], 'old gzip changed')
            for mode in ('off', 'on'):
                child_env = {**environment, 'GOLD_FEATURES_ROOT': str(root), 'GOLD_FEATURES_MODE': mode,
                             'COOP_POOL': row['pool'], 'COOP_SPLIT': 'train', 'COOP_POOL_SEED': '313',
                             'COOP_TEAM': ','.join(TEAMS[row['pool']])}
                raw = capture(command, child_env, dict(cmd='demo', seed=row['seed'], mode='coop',
                              difficulty='ABYSS', budget=128), deadline, args.max_bytes)
                compressed = gzip.compress(raw, compresslevel=3, mtime=0)
                name = f"{row['pool']}-{row['seed']}-{mode}.json.gz"
                publish(name, compressed, reserve)
                result = dict(mode=mode, seed=row['seed'], pool=row['pool'], status='pending',
                    source=str(path), sourceSHA256=row['artifactSHA256'], artifact=name,
                    artifactSHA256=sha(compressed), bytes=len(compressed), rawSHA256=sha(raw), rawBytes=len(raw))
                record['results'].append(result)
                try:
                    verify_recapture(old, parse(raw), mode)
                except Exception:
                    result['status'] = 'rejected'
                    raise
                result.update(status='verified',
                              comparison='whole old response equal after removing only schema/global extension')
        require(time.monotonic() < deadline, 'recapture total time limit')
        source_bindings(manifest, root, collector)
        require(read_bounded(manifest_path, args.max_bytes) == manifest_raw and
                read_bounded(selection_path, args.max_bytes) == selection_raw, 'frozen inputs changed')
        require(all(sha(read_bounded(p, args.max_bytes)) == r['artifactSHA256'] for p, r in plan), 'old gzip changed')
        require(all(sha((HERE / name).read_bytes()) == digest for name, digest in record['implementation'].items()),
                'gold implementation changed')
        require(time.monotonic() < deadline, 'recapture total time limit after verification')
        record['status'] = 'complete'
    except Exception as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {str(exc)[:1200]}')
        raise
    finally:
        record['finishedAt'] = clock()
        publish('manifest.json', (json.dumps(record, ensure_ascii=False) + '\n').encode())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(f'{type(error).__name__}: {str(error)[:1200]}')
