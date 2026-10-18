"""Train paired frozen-partner arms, then evaluate only complete, frozen weights."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--steps', type=int, default=16384)
    args = parser.parse_args()
    if not re.fullmatch(r'coop-partner-[A-Za-z0-9_-]+', args.name):
        parser.error('Use a coop-partner- prefixed experiment name')
    if args.steps < 256 or args.steps % 256:
        parser.error('steps must be a positive multiple of 256')
    root = args.root.resolve()
    out = root / 'experiments' / args.name
    out.mkdir(exist_ok=False)
    code = root / 'experiment-code'
    base = root / 'experiments/bc-expansion-v1/all.pt'
    python = str(root / 'venvs/learner-20261018/bin/python3')
    clock = lambda: datetime.now(timezone.utc).isoformat()
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    sources = [p for directory in ('rl', 'parallel-rl', 'coop', 'reward')
               for p in (code / directory).iterdir()
               if p.is_file() and p.suffix in ('.mjs', '.py', '.json')]
    sources += [Path(__file__).resolve(), root / 'tools/evaluate-models.py']
    hashes = {str(p): sha(p) for p in sources}
    config = json.loads((code / 'rl/config.json').read_text())
    config.update(gamma=1.0, lr=.0001, rollout_steps=64)
    (out / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    manifest = dict(status='running', startedAt=clock(),
                    primaryMetric='complete ABYSS team summary.victory',
                    kind='normal versus frozen heterogeneous heuristic partners; not learned FCP',
                    sourceHEAD=subprocess.check_output(['git', '-C', str(code), 'rev-parse', 'HEAD'], text=True).strip(),
                    sourceSHA256=hashes, checkpointInputSHA256=sha(base),
                    config=config, stepsPerArm=args.steps, actorsPerArm=4,
                    seedBase=50001, seedStride=4096, episodesPerActor=512,
                    training=[], crossplay=[], crossplayPlannedGames=128,
                    mapSeeds='development regression 1..16; validation and sealed test untouched',
                    heldoutPartner='random-v1 family absent from training')

    def save():
        temporary = out / 'manifest.json.tmp'
        temporary.write_text(json.dumps(manifest, indent=2) + '\n')
        temporary.replace(out / 'manifest.json')

    def unchanged():
        if any(sha(p) != value for p, value in hashes.items()) or sha(base) != manifest['checkpointInputSHA256']:
            raise RuntimeError('Source or initialization checkpoint changed during study')

    def environment(pool, split, team=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('COOP_', 'ARK_'))}
        env.update(COOP_POOL=pool, COOP_SPLIT=split, COOP_POOL_SEED='313' if split == 'train' else '7919',
                   STRONGHOLD_GAME=str(root / 'game'), OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   PYTHONDONTWRITEBYTECODE='1')
        if team:
            env['COOP_TEAM'] = team
        return env

    def execute(command, env, log):
        with log.open('w') as stream:
            result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=10800)
        return result.returncode

    def train(label):
        env = environment(label, 'train', 'original-v1,buy2-v1,saver-v1' if label == 'mixed' else None)
        command = [python, str(code / 'parallel-rl/train.py'), '--checkpoint', str(base), '--out', str(out / (label + '.pt')),
                   '--config', str(out / 'config.json'), '--workers', '4', '--steps', str(args.steps), '--rollout', '64',
                   '--seed', '50001', '--episodes-per-worker', '512', '--mode', 'coop', '--difficulty', 'ABYSS',
                   '--env-command', f'node {code}/coop/env.mjs --game {root}/game', '--reward-mode', 'terminal/v1',
                   '--provenance-file', str(code / 'coop/pool.json')]
        record = dict(arm=label, command=command, startedAt=clock(),
                      environment={k: v for k, v in env.items() if k.startswith('COOP_')})
        record.update(exitCode=execute(command, env, out / (label + '.log')), finishedAt=clock())
        sidecar = out / (label + '.pt.manifest.json')
        status = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        record.update(status=status.get('status', 'missing'), totalSteps=status.get('totalSteps', 0),
                      completedEpisodes=status.get('completedEpisodes', 0))
        return record

    def crossplay(label, pool, team):
        env = environment(pool, 'eval', team)
        command = [python, str(root / 'tools/evaluate-models.py'), '--root', str(root), '--checkpoints', str(out),
                   '--names', 'normal', 'mixed', '--out', str(out / 'crossplay' / label), '--difficulties', 'ABYSS',
                   '--parallel', '2', '--env-command', f'node {code}/coop/env.mjs --game {root}/game']
        return dict(family=label, pool=pool, team=team, command=command,
                    exitCode=execute(command, env, out / (label + '-eval.log')), finishedAt=clock())

    save()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            for future in as_completed([executor.submit(train, n) for n in ('normal', 'mixed')]):
                record = future.result()
                manifest['training'].append(record)
                save()
                print(json.dumps(record), flush=True)
        unchanged()
        if any(r['exitCode'] or r['status'] != 'complete' or r['totalSteps'] != args.steps for r in manifest['training']):
            raise RuntimeError('Training incomplete; partial weights will not be evaluated')
        manifest['checkpointSHA256'] = {n: sha(out / (n + '.pt')) for n in ('normal', 'mixed')}
        manifest['status'] = 'evaluating'
        save()
        families = [('original', 'normal', 'original-v1'), ('weak-buy2', 'weak', 'buy2-v1'),
                    ('weak-saver', 'weak', 'saver-v1'), ('heldout-random', 'heldout', 'random-v1')]
        with ThreadPoolExecutor(max_workers=4) as executor:
            for future in as_completed([executor.submit(crossplay, n, pool, ','.join([partner] * 3))
                                        for n, pool, partner in families]):
                record = future.result()
                manifest['crossplay'].append(record)
                save()
                print(json.dumps(record), flush=True)
        unchanged()
        if any(sha(out / (n + '.pt')) != value for n, value in manifest['checkpointSHA256'].items()):
            raise RuntimeError('Evaluated weights changed')
        if any(r['exitCode'] for r in manifest['crossplay']):
            raise RuntimeError('Cross-play incomplete; inspect retained evaluation manifests')
        manifest['status'] = 'complete'
    except Exception as error:
        manifest.update(status='failed', error=repr(error))
        raise
    finally:
        manifest['finishedAt'] = clock()
        save()


if __name__ == '__main__':
    main()
