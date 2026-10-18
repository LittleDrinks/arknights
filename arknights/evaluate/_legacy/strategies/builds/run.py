#!/usr/bin/env python3
"""Sequential natural botbench runner. Local defaults: <=8 games, <=39s child wall.

Server batches explicitly opt in with --server, and still need a finite --seconds.
No band override, fixtures, seed rejection, rehearsal, installs or remote calls.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
GAME = Path(os.environ.get('ARK_BUILD_GAME_ROOT', str(HERE.parent.parent / 'Stronghold-Protocol'))).resolve()
PIN = '6ab2468279e926d107788f63d7fe219b85f67b4a'
CASES = {
    'upstream': None,
    'off': {'ARK_BUILD_TEACHER': 'off'},
    'modules-off': {'ARK_BUILD_TEACHER': 'sargon', 'ARK_BUILD_ROSTER': '0', 'ARK_BUILD_EQUIPMENT': '0', 'ARK_BUILD_ADJACENCY': '0'},
    'sargon': {'ARK_BUILD_TEACHER': 'sargon'},
    'kjerag': {'ARK_BUILD_TEACHER': 'kjerag'},
    'sargon-no-equipment': {'ARK_BUILD_TEACHER': 'sargon', 'ARK_BUILD_EQUIPMENT': '0'},
    'kjerag-no-adjacency': {'ARK_BUILD_TEACHER': 'kjerag', 'ARK_BUILD_ADJACENCY': '0'},
    'sargon-no-roster': {'ARK_BUILD_TEACHER': 'sargon', 'ARK_BUILD_ROSTER': '0'},
}

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def preflight():
    pin = subprocess.check_output(['git', '-C', str(GAME), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != PIN:
        raise RuntimeError('game HEAD mismatch')
    dirty = subprocess.check_output(['git', '-C', str(GAME), 'status', '--porcelain', '--untracked-files=all', '--',
                                    'server', 'shared', 'data', 'tools/botbench.mjs'], text=True).strip()
    if dirty:
        raise RuntimeError('game runtime inputs are dirty')
    manifest = json.loads((HERE / 'source-hashes.json').read_text())
    for rel, sha in manifest['game'].items():
        if digest(GAME / rel) != sha:
            raise RuntimeError(f'source mismatch: {rel}')

def size_tree(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())

def write_ledger(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n')
    temp.replace(path)

def rss(pid):
    try:
        lines = Path(f'/proc/{pid}/status').read_text().splitlines()
        return next(int(s.split()[1]) * 1024 for s in lines if s.startswith('VmRSS:'))
    except (FileNotFoundError, ProcessLookupError, StopIteration):
        return 0

def normalized(value):
    # The benchmark's timing fields and observer metadata are not game outcomes.
    ignored = {'ms', 'cpu', 'rehMs', 'rehCpu', 'maxRssKiB', 'config'}
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items() if k not in ignored}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value

def summarize(directory, records):
    summary = []
    for row in records:
        trace = directory / (row['label'] + '.jsonl')
        events, parse_errors = [], []
        if trace.exists():
            for line, text in enumerate(trace.read_text().splitlines(), 1):
                try:
                    events.append(json.loads(text))
                except json.JSONDecodeError:
                    parse_errors.append(f'trace line {line}: truncated/invalid JSON')
        raw = directory / (row['label'] + '.json')
        try:
            results = json.loads(raw.read_text()) if raw.exists() else []
        except json.JSONDecodeError:
            results = []
            parse_errors.append('benchmark result truncated/invalid JSON')
        preps = [e for e in events if e['type'] == 'prep']
        finishes = [e for e in events if e['type'] == 'finish']
        actions = [e for e in events if e['type'] == 'action']
        summary.append({**row, 'completed': bool(finishes), 'natural': True, 'parseErrors':parse_errors,
            'victory': results[0]['victory'] if results else None,
            'roundsPassed': results[0]['roundsPassed'] if results else None,
            'hiddenReached': results[0]['hidden'] if results else None,
            'errors': results[0]['errors'] if results else None,
            'refusedActions': sum(not a.get('result', {}).get('ok', False) for a in actions),
            'nativeTempDiscards': sum(len(e['removed']) for e in events if e['type'] == 'native-temp-discard'),
            'final': finishes[-1] if finishes else None,
            'rounds': [{'round':e['round'], 'level':e['state']['level'], 'lp':e['state']['lp'],
                'units':e['state']['units'], 'layers':e['state']['layers'], 'core':e['state']['core'],
                'equipment':[{ 'name':p['name'], 'items':p['items']} for p in e['state']['board'] if p['items']],
                'adjacency':e['state']['adjacency']} for e in preps]})
    controls = {}
    completed = {c['label'] for c in summary if c['completed'] and not c['parseErrors'] and c['status'] == 'complete'}
    for seed in sorted({r['seed'] for r in records if 'seed' in r}):
        for target in ['off', 'modules-off']:
            if not {f'upstream-{seed}', f'{target}-{seed}'}.issubset(completed):
                continue
            a, b = directory / f'upstream-{seed}.json', directory / f'{target}-{seed}.json'
            ta, tb = directory / f'upstream-{seed}.jsonl', directory / f'{target}-{seed}.jsonl'
            controls[target if seed == 1 else f'{target}:{seed}'] = {
                'outcomesEqual': normalized(json.loads(a.read_text())) == normalized(json.loads(b.read_text())),
                'fullTraceEqual': normalized([json.loads(s) for s in ta.read_text().splitlines()])
                    == normalized([json.loads(s) for s in tb.read_text().splitlines()]),
            }
    return {'cases':summary, 'realControls':controls}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='results')
    parser.add_argument('--cases', default=','.join(CASES))
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--seeds', type=int, default=1)
    parser.add_argument('--seconds', type=float, default=39)
    parser.add_argument('--output-mib', type=int, default=16)
    parser.add_argument('--server', action='store_true')
    args = parser.parse_args()
    cases = args.cases.split(',')
    if any(c not in CASES for c in cases) or len(set(cases)) != len(cases) or args.seeds < 1 or args.seed < 1 or args.output_mib < 1 or not (0 < args.seconds < float('inf')):
        parser.error('invalid cases/seed/seeds/seconds')
    if not args.server and (len(cases) * args.seeds > 8 or args.seconds > 39 or args.output_mib > 16):
        parser.error('local limit is eight games and 39 seconds; larger batches are server-only')
    out = (HERE / args.out).resolve()
    if not out.is_relative_to(HERE) or out == HERE:
        parser.error('output must be a new subdirectory of build-teacher/')
    if out.exists() and any(out.iterdir()):
        parser.error('output directory is nonempty; preserving prior evidence')
    if not args.server:
        prior_seconds, prior_games = 0.0, 0
        for path in HERE.rglob('budget.json'):
            ledger = json.loads(path.read_text())
            if ledger.get('server'):
                continue
            if any(r.get('status') == 'running' for r in ledger.get('records', [])):
                parser.error('unfinished local ledger; inspect it before another simulation')
            prior_seconds += ledger.get('childWallSeconds', 0)
            prior_games += sum('command' in r for r in ledger.get('records', []))
        if prior_games + len(cases) * args.seeds > 8 or prior_seconds >= 39:
            parser.error('cumulative local eight-game/39-second budget exhausted; do not rerun locally')
        args.seconds = min(args.seconds, 39 - prior_seconds)
    preflight()
    out.mkdir(parents=True, exist_ok=True)
    env = {k:v for k,v in os.environ.items() if not k.startswith('ARK_') and k != 'NODE_OPTIONS'}
    env['ARK_BUILD_GAME_ROOT'] = str(GAME)
    records = [{'label':f'{case}-{seed}', 'case':case, 'seed':seed, 'status':'not-run'}
               for case in cases for seed in range(args.seed, args.seed + args.seeds)]
    total, peak, reason = 0.0, 0, None
    # Persist the ENTIRE planned denominator before any child can fail or time out.
    write_ledger(out / 'plan.json', {'plannedGames':len(records), 'records':records})
    write_ledger(out / 'budget.json', {'plannedGames':len(records), 'childWallSeconds':0,
        'server':args.server, 'records':records})
    for record in records:
        case, seed = record['case'], record['seed']
        if reason or total >= args.seconds - 0.25:
            record.update(status='not-run-budget', stopReason=reason or 'time-budget')
            continue
        label = f'{case}-{seed}'
        child_env = {**env, **(CASES[case] or {}), 'ARK_BUILD_TRACE':str(out / f'{label}.jsonl')}
        cmd = ['node', '--max-old-space-size=384']
        if case != 'upstream':
            cmd += ['--loader', str(HERE / 'loader.mjs')]
        cmd += ['--import', str(HERE / 'observe.mjs'), str(GAME / 'tools/botbench.mjs'),
                '--configs', 'solo:ABYSS', '--seed', str(seed), '--seeds', '1', '--rehearsal', '0',
                '--jobs', '1', '--json', str(out / f'{label}.json'), '--quiet']
        record.update(status='running', command=cmd)
        # Ledger written BEFORE spawning, so interrupted attempts remain visible.
        write_ledger(out / 'budget.json', {'plannedGames':len(records), 'childWallSeconds':total, 'server':args.server, 'records':records})
        started = time.monotonic()
        reason = None
        with (out / f'{label}.log').open('wb') as log:
            proc = subprocess.Popen(cmd, env=child_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                current_rss = rss(proc.pid) + rss(os.getpid())
                peak = max(peak, current_rss)
                # Margins below user maxima leave room for polling/termination/summary.
                if total + elapsed >= args.seconds: reason = 'timeout'
                elif current_rss > 700 * 1024**2: reason = 'rss-limit'
                elif size_tree(out) > args.output_mib * 1024**2: reason = 'output-limit'
                if reason:
                    os.killpg(proc.pid, signal.SIGKILL)
                    break
                time.sleep(0.025)
            proc.wait()
        elapsed = time.monotonic() - started
        total += elapsed
        record.update(status=reason or ('complete' if proc.returncode == 0 else 'failed'),
                      exit=proc.returncode, childWallSeconds=elapsed)
        ledger = {'plannedGames':len(records), 'childWallSeconds':total, 'peakSampledAggregateRssBytes':peak,
            'childHighWaterRssKiB':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            'outputBytes':size_tree(out), 'server':args.server, 'records':records}
        write_ledger(out / 'budget.json', ledger)
        print(f'{label}: {record["status"]}; cumulative child wall {total:.3f}s', flush=True)
    result = summarize(out, records)
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    # Include the derived summary in the final output measurement.
    ledger = {'plannedGames':len(records), 'childWallSeconds':total, 'peakSampledAggregateRssBytes':peak,
        'childHighWaterRssKiB':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        'outputBytes':size_tree(out), 'server':args.server, 'records':records}
    write_ledger(out / 'budget.json', ledger)
    print(json.dumps({'realControls':result['realControls'], 'outputBytes':size_tree(out)}, ensure_ascii=False))
    controls_ok = all(all(v.values()) for v in result['realControls'].values())
    bad_runs = any(c.get('status') != 'complete' or not c.get('completed') or c.get('errors') or c.get('parseErrors') for c in result['cases'])
    raise SystemExit(0 if controls_ok and not bad_runs else 1)

if __name__ == '__main__':
    main()
