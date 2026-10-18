"""Run the reviewed natural-build teacher matrix on development seeds, with frozen inputs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--name', required=True)
args = parser.parse_args()
if not args.name.replace('-', '').replace('_', '').isalnum():
    parser.error('name must be a single directory name')
root = args.root.resolve()
code = root / 'experiment-code/build-teacher'
out = root / 'experiments' / args.name
out.mkdir(exist_ok=False)
clock = lambda: datetime.now(timezone.utc).isoformat()
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
sources = {str(p): sha(p) for p in code.iterdir() if p.is_file() and p.suffix in ('.mjs', '.py', '.json')}
sources[str(Path(__file__).resolve())] = sha(__file__)
manifest = dict(status='running', startedAt=clock(), kind='natural full-game development teacher ablation',
                plannedGames=128, seeds=list(range(1, 17)), difficulty='ABYSS', primaryMetric='complete summary.victory',
                teacherNotTrainedModel=True, sourceSHA256=sources, batches=[], results=[], controls=[])


def save():
    temp = out / 'manifest.json.tmp'
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    temp.replace(out / 'manifest.json')


def run(seed):
    relative = 'server-' + args.name + '/s' + str(seed)
    command = ['python3', str(code / 'run.py'), '--server', '--seed', str(seed), '--seeds', '4',
               '--seconds', '1800', '--output-mib', '256', '--out', relative]
    env = {k: v for k, v in os.environ.items() if not k.startswith(('ARK_', 'COOP_'))}
    env.update(ARK_BUILD_GAME_ROOT=str(root / 'game'), PYTHONDONTWRITEBYTECODE='1')
    record = dict(seedStart=seed, plannedGames=32, command=command, startedAt=clock(), output=str(code / relative))
    with (out / ('s' + str(seed) + '.log')).open('w') as stream:
        result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1900)
    record.update(exitCode=result.returncode, finishedAt=clock())
    return record


save()
try:
    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(run, seed) for seed in (1, 5, 9, 13)]):
            record = future.result()
            manifest['batches'].append(record)
            save()
            print(json.dumps(record), flush=True)
    if any(sha(p) != value for p, value in sources.items()):
        raise RuntimeError('Reviewed teacher source changed during matrix')
    # Import the exact runner normalizer to check every paired off control,
    # including seeds beyond the runner's one-seed control convenience path.
    spec = importlib.util.spec_from_file_location('build_matrix_contract', code / 'run.py')
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    grouped = {case: [] for case in contract.CASES}
    for batch in manifest['batches']:
        folder = Path(batch['output'])
        summary = json.loads((folder / 'summary.json').read_text())
        for row in summary['cases']:
            grouped[row['case']].append(row)
        for seed in range(batch['seedStart'], batch['seedStart'] + 4):
            for case in ('off', 'modules-off'):
                pairs = [(folder / f'upstream-{seed}.json', folder / f'{case}-{seed}.json'),
                         (folder / f'upstream-{seed}.jsonl', folder / f'{case}-{seed}.jsonl')]
                equal = []
                for left, right in pairs:
                    def read(p):
                        return [json.loads(s) for s in p.read_text().splitlines()] if p.suffix == '.jsonl' else json.loads(p.read_text())
                    equal.append(contract.normalized(read(left)) == contract.normalized(read(right)))
                manifest['controls'].append(dict(seed=seed, case=case, outcomeEqual=equal[0], traceEqual=equal[1]))
    for case, rows in grouped.items():
        rows.sort(key=lambda r: r['seed'])
        valid = lambda row: row['status'] == 'complete' and row.get('completed') and row.get('errors') == 0
        wins = [r['seed'] for r in rows if valid(r) and r.get('victory') is True]
        failures = [dict(seed=r['seed'], status=r['status'], parseErrors=r.get('parseErrors')) for r in rows if not valid(r)]
        manifest['results'].append(dict(case=case, plannedGames=16, completedGames=sum(valid(r) for r in rows),
                                        wins=len(wins), winningSeeds=wins, failures=failures,
                                        exactSeedCoverage=[r['seed'] for r in rows] == list(range(1, 17))))
    manifest['artifactSHA256'] = {str(p): sha(p) for b in manifest['batches'] for p in Path(b['output']).iterdir() if p.is_file()}
    if any(b['exitCode'] for b in manifest['batches']) or any(r['failures'] or not r['exactSeedCoverage'] for r in manifest['results']):
        raise RuntimeError('Incomplete natural matrix; retained all planned denominators')
    if not all(r['outcomeEqual'] and r['traceEqual'] for r in manifest['controls']):
        raise RuntimeError('Default off control changed actual game behavior')
    manifest['status'] = 'complete'
except Exception as error:
    manifest.update(status='failed', error=repr(error))
    raise
finally:
    manifest['finishedAt'] = clock()
    save()
