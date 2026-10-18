#!/usr/bin/env python3
"""Frozen-model dev128 prep timing ablation; no training or teacher-seed mode."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CODE = HERE.parent
# Reuse helpers without replacing any coop-items/common/evaluate globals.
_spec = importlib.util.spec_from_file_location('prep_timing_helpers', CODE / 'coop-items/run.py')
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)
C, E = H.C, H.E
write_new, file_identity = H.write_new, H.file_identity
parameter_schema, admit_models, game_identity = H.parameter_schema, H.admit_models, H.game_identity

BASE = '25c6af63cbb819bb4a4ed9682bed2824f27a4ec5'
MODELS = ('parent', 'candidate')
VERSIONS = {
    'off': 'coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1',
    'deferred': 'coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1+prep-timing/deferred-v1',
}
MIB = 1024 * 1024
RESULT_RESERVE = 128 * 1024
RUN_RESERVE = 8 * MIB
MODEL_SHA256 = {
    'parent': 'f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a',
    'candidate': '397e8780202bbcdb37ad0f4a29e38df3e78ed6c3369d03cab6748aab5a5c2dc3',
}
LIMITATIONS = [
    'Timing-module approximation only, not exact original teacher prep.',
    'Free rewards/equipment retain v1 policy; temp fallback still rearranges; controller changes RNG paths.',
    'Ready candidates are determined BEFORE control; canReady false AFTER control strictly fails, never forces illegal ready.',
    'Only current actual features reach the actor; no teacher labels, outcomes, timing ID or checkpoint metadata.',
    '128 scheduled episodes reuse 16 development seeds; not 128 independent samples.',
    'Original/mixed are known teammate families, not heldout-family evidence.',
    'Retained native arrange/draft paths can depend on the pool; no end-to-end visible-information claim.',
    'Team wins and learner wins are separate; all failures/stops/unrun remain in denominators.',
]


def check_files(paths, expected):
    C.require(expected == MODEL_SHA256, 'Only the fixed parent/naturalWins SHA256 pair is allowed')
    return H.check_files(paths, expected)


class RunDeadline(BaseException):
    """Signal expiry that cannot be swallowed by imported Exception handlers."""


def schedule():
    return [dict(id=f'{model}-{variant}-{pool}-{seed:02}', model=model, controller=variant,
                 pool=pool, seed=seed, mode='coop', difficulty='ABYSS', seat=0,
                 status='unrun', steps=0, eof=False, error=None, teamVictory=None,
                 learnerIndividualVictory=None)
            for model in MODELS for variant in VERSIONS for pool in C.TEAMS for seed in range(1, 17)]


def summary(rows):
    C.require([r['id'] for r in rows] == [r['id'] for r in schedule()], 'Fixed 128 schedule/order')
    def count(rs):
        wins = sum(r['status'] == 'complete' and r['teamVictory'] is True for r in rs)
        own = sum(r['status'] == 'complete' and r['learnerIndividualVictory'] is True for r in rs)
        return dict(denominator=len(rs), **{s: sum(r['status'] == s for r in rs)
                    for s in ('complete', 'failed', 'stopped', 'unrun')}, teamWins=wins, learnerWins=own,
                    teamWinsPerScheduled=wins / len(rs), learnerWinsPerScheduled=own / len(rs))
    return {**count(rows), 'cells': {f'{m}/{v}/{p}': count([r for r in rows if
            (r['model'], r['controller'], r['pool']) == (m, v, p)])
            for m in MODELS for v in VERSIONS for p in C.TEAMS}}


def inventory(directory, deadline=math.inf):
    result = {}
    def walk_error(error):
        raise error
    for parent, dirs, files in os.walk(directory, onerror=walk_error, followlinks=False):
        for name in sorted(dirs + files):
            p = Path(parent) / name
            C.require(not p.is_symlink(), 'Symlink in evidence/source')
            if name in dirs:
                continue
            result[p.relative_to(directory).as_posix()] = file_identity(p, deadline)
    return result


def source_identity():
    pins = C.parse((HERE / 'source-pins.json').read_bytes())
    C.require(pins['base'] == BASE, 'Source base pin')
    original = {}
    for folder in ('rl', 'coop'):
        original.update({folder + '/' + k: v for k, v in inventory(CODE / folder).items()})
    for name in pins['helpers']:
        original[name] = file_identity(CODE / name)
    C.require(original == pins['files'], 'Original source set/size/SHA drift')
    head = subprocess.check_output(['git', '-C', str(CODE), 'rev-parse', 'HEAD'], text=True, timeout=5).strip()
    subprocess.run(['git', '-C', str(CODE), 'merge-base', '--is-ancestor', BASE, head], check=True, timeout=5)
    own = inventory(HERE)
    result = dict(base=BASE, codeCommit=head, original=original, implementation=own)
    return {**result, 'sha256': C.digest(C.encoded(result))}


def child_env(row):
    env = C.clean_env()
    # Do not inherit other ARK experiments. There is exactly one explicit loader.
    env = {k: v for k, v in env.items() if not k.startswith('ARK_')}
    env.update(ARK_PREP_TIMING=row['controller'], COOP_POOL='normal' if row['pool'] == 'original' else 'mixed',
               COOP_SPLIT='eval', COOP_POOL_SEED='313', COOP_TEAM=','.join(C.TEAMS[row['pool']]))
    return env


def game_job(row, model, ckpt, game, out, deadline, byte_limit, pool_manifest, protocol=E.Protocol):
    result = dict(row)
    directory = out / row['id']
    directory.mkdir(exist_ok=False)
    channel, steps, started, state = None, 0, time.monotonic(), None
    try:
        command = ['node', '--no-warnings', '--loader', str(HERE / 'loader.mjs'),
                   str(CODE / 'coop/env.mjs'), '--game', str(game)]
        channel = protocol(command, child_env(row), directory, deadline, byte_limit - RESULT_RESERVE)
        request = dict(cmd='reset', seed=row['seed'], mode='coop', difficulty='ABYSS', budget=128, teacher=False)
        while True:
            state, raw = channel.request(request)
            C.require(type(state) is dict, 'Unknown response type')
            if state.get('done') is True:
                channel.charge(len(raw))
                with (directory / 'terminal.json').open('xb') as stream:
                    stream.write(raw)  # Actual full terminal, even if its contract later fails.
            C.require('PREP_TIMING_STOP' not in str(state.get('error', '')), 'PREP_TIMING_STOP')
            E.state_contract(state, row, ckpt, steps, pool_manifest)
            C.require(state['controller'] == VERSIONS[row['controller']], 'Exact coop/controller version required')
            if state['done']:
                own = C.terminal_contract(state, seed=row['seed'], steps=steps, schema=ckpt['schema'])
                channel.finish()  # Close ack, no extra raw, full EOF, zero exit/stderr.
                result.update(status='complete', eof=True, teamVictory=state['summary']['victory'],
                              learnerIndividualVictory=own['victory'],
                              learnerOutcome={k: own[k] for k in ('victory', 'alive', 'lp', 'roundsPassed', 'eliminatedRound')},
                              rounds=state['rounds'])
                break
            C.require(steps < 4096, 'Maximum policy steps; no terminal result')
            action, _, _ = model.act(state['features'], greedy=True)
            C.require(type(action) is int and 0 <= action < len(state['candidates']) and
                      state['features']['mask'][action], 'Illegal greedy action')
            channel.action(dict(step=steps, round=state['rounds'], seat=0, candidates=state['candidates'],
                                mask=state['features']['mask'], candidateIndex=action, action=state['candidates'][action]))
            request = dict(cmd='step', candidateIndex=action)
            steps += 1
    except (Exception, RunDeadline) as exc:
        result.update(status='stopped' if 'PREP_TIMING_STOP' in str(exc) else 'failed',
                      error=f'{type(exc).__name__}: {exc}'[:4000], eof=False)
    finally:
        if channel:
            try:
                channel.close()
            except Exception as exc:
                result.update(status='failed', error='Protocol cleanup: ' + str(exc)[:4000], eof=False)
        if isinstance(state, dict):
            for key in ('engineErrors', 'actionErrors', 'controllerErrors', 'controllerDiagnosticErrors',
                        'partnerActionErrors', 'controllerBlockedMoves', 'fatalRejections'):
                result[key] = state.get(key)
        result.update(steps=steps, seconds=time.monotonic() - started, outputBytes=channel.bytes if channel else 0)
        result['files'] = inventory(directory)
        write_new(directory / 'result.json', result, RESULT_RESERVE)
    return result, inventory(directory)


def seal_manifest(out):
    C.require(out.is_dir() and not out.is_symlink(), 'Output root directory symlink/replacement')
    stat = out.stat()
    return {**file_identity(out / 'manifest.json'), 'directoryDevice': stat.st_dev, 'directoryInode': stat.st_ino}


def audit_evidence(out, rows, seals, manifest_identity, byte_limit, deadline=math.inf):
    C.require(time.monotonic() < deadline, 'Evidence audit deadline')
    C.require(out.is_dir() and not out.is_symlink(), 'Output root directory symlink/replacement')
    stat = out.stat()
    C.require((stat.st_dev, stat.st_ino) == (manifest_identity['directoryDevice'], manifest_identity['directoryInode']),
              'Output root directory identity drift')
    expected_names = {'manifest.json'} | set(seals)
    C.require({p.name for p in out.iterdir()} == expected_names, 'Run file/directory set drift')
    C.require(file_identity(out / 'manifest.json', deadline) ==
              {k: manifest_identity[k] for k in ('bytes', 'sha256')}, 'Manifest SHA/size drift')
    by_id = {r['id']: r for r in rows}
    for ident, recorded in seals.items():
        C.require((out / ident).is_dir() and not (out / ident).is_symlink(),
                  'Evidence episode directory replaced or symlinked: ' + ident)
        C.require(all(p.is_file() and not p.is_symlink() for p in (out / ident).iterdir()),
                  'Unexpected evidence directory/symlink')
        current = inventory(out / ident, deadline)
        C.require(current == recorded, 'Evidence file set/size/SHA drift: ' + ident)
        C.require(sum(v['bytes'] for v in current.values()) <= byte_limit, 'Per-game total evidence budget')
        metadata = C.parse((out / ident / 'result.json').read_bytes())
        C.require(metadata == by_id[ident], 'Result metadata drift: ' + ident)
        C.require(metadata['files'] == {k: v for k, v in recorded.items() if k != 'result.json'},
                  'Result file metadata mismatch: ' + ident)
    C.require(sum(v['bytes'] for files in seals.values() for v in files.values()) +
              manifest_identity['bytes'] <= 128 * byte_limit + RUN_RESERVE, 'Total output budget')


def finalize(rows, out, seals, manifest_identity, byte_limit, guard, prior_error=None, deadline=math.inf):
    error = prior_error
    try:
        guard()
        audit_evidence(out, rows, seals, manifest_identity, byte_limit, deadline)
    except (Exception, RunDeadline) as exc:
        error = f'{type(exc).__name__}: {exc}'[:4000]
    if error:
        # Keep per-game evidence immutable. The authoritative run summary revokes ALL credit.
        for row in rows:
            if row['status'] == 'complete':
                row.update(status='failed', error='Run invalidated: ' + error,
                           teamVictory=None, learnerIndividualVictory=None, creditRevoked=True)
    return dict(status='complete' if not error and all(r['status'] == 'complete' for r in rows) else 'failed',
                integrity='rejected' if error else 'verified', error=error, **summary(rows), games=rows,
                evidence=seals, manifest=manifest_identity)


def execute(args):
    C.require(sys.platform.startswith('linux') and hasattr(os, 'sched_setaffinity'), 'Run server CLI on Linux/WSL')
    C.require(math.isfinite(args.timeout) and 0 < args.timeout <= 120 and
              math.isfinite(args.total_timeout) and 0 < args.total_timeout <= 1800 and
              1 <= args.output_mib <= 64 and 1 <= args.cpus <= 4, 'Finite resource bounds')
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:args.cpus])
    root = args.root.resolve()
    target = Path(args.out)
    target = target if target.is_absolute() else root / target
    # Evidence must not live inside inputs/code/game, including symlink-resolved ancestors.
    resolved = target.resolve()
    paths = {name: C.relative(root, getattr(args, name)) for name in MODELS}
    protected = [CODE / d for d in ('prep-timing', 'coop-items', 'rl', 'coop', 'survivor-study', 'policy')]
    for forbidden in (*protected, root / 'game', *paths.values()):
        C.require(resolved != forbidden.resolve() and forbidden.resolve() not in resolved.parents,
                  'Output must be outside code/game/checkpoint directories')
    out = C.new_output(target)
    rows, seals, loaded = schedule(), {}, {}
    manifest = dict(kind='prep-timing/dev128-v1', base=BASE, games=schedule(), denominator=128,
                    greedy=True, teacher=False, poolSeed=313, split='eval', teams=C.TEAMS,
                    controllers=VERSIONS, expectedModelSHA256=MODEL_SHA256, limitations=LIMITATIONS, sequential=True,
                    limits=dict(cpuAffinity=sorted(os.sched_getaffinity(0)), gameSeconds=args.timeout,
                                totalSeconds=args.total_timeout, perGameBytes=args.output_mib * MIB,
                                totalBytes=128 * args.output_mib * MIB + RUN_RESERVE,
                                metadataReserveBytes=RUN_RESERVE, maxSteps=4096))
    manifest_identity, error = None, None
    started = time.monotonic()
    deadline = started + args.total_timeout
    def alarm(_signum, _frame):
        if time.monotonic() >= deadline:
            raise RunDeadline('Total evaluation deadline')
        raise TimeoutError('Absolute game deadline')
    old_handler = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, args.total_timeout)
    try:
        files = check_files(paths, {n: getattr(args, n + '_sha256') for n in MODELS})
        source = source_identity()
        L = C.learner(CODE)
        loaded, identities = admit_models(L, paths, files)
        game_path, game = game_identity(root)
        C.require(all(c['schema'] == game['schema'] for _, c in loaded.values()), 'Game/checkpoint schema mismatch')
        manifest.update(source=source, models=identities, game=game)
        write_new(out / 'manifest.json', manifest)
        manifest_identity = seal_manifest(out)
        pool_manifest = C.digest(json.dumps(C.parse((CODE / 'coop/pool.json').read_bytes()), separators=(',', ':')).encode())
        for i, row in enumerate(rows):
            C.require(time.monotonic() < deadline, 'Total evaluation deadline')
            signal.setitimer(signal.ITIMER_REAL, min(args.timeout, deadline - time.monotonic()))
            rows[i], seals[row['id']] = game_job(row, *loaded[row['model']], game_path, out,
                                                min(deadline, time.monotonic() + args.timeout),
                                                args.output_mib * MIB, pool_manifest)
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, deadline - time.monotonic()))
        def guard():
            C.require(source_identity() == source, 'Source identity drift')
            C.require(game_identity(root) == (game_path, game), '486 rules/static data drift')
            for name, (model, ckpt) in loaded.items():
                C.require(file_identity(paths[name]) == files[name] and L.digest(model) == identities[name]['parameterSHA256']
                          and parameter_schema(model) == identities[name]['parameterSchema'] and
                          ckpt['schema'] == identities[name]['schema'] and not model.training and
                          all(not p.requires_grad for p in model.parameters()), 'Frozen checkpoint/model drift')
        report = finalize(rows, out, seals, manifest_identity, args.output_mib * MIB, guard, deadline=deadline)
    except (Exception, RunDeadline) as exc:
        error = f'{type(exc).__name__}: {exc}'[:4000]
        # A global interruption can leave a completed failure result not yet returned to the loop.
        # Do not adopt any new on-disk metadata as trusted. Preserve it but reject all run credit.
        if manifest_identity is None and not (out / 'manifest.json').exists():
            manifest.update(admissionFailed=True, error=error)
            write_new(out / 'manifest.json', manifest)
            manifest_identity = seal_manifest(out)
        for row in rows:
            if row['status'] == 'unrun' and (out / row['id']).exists():
                row.update(status='failed', error='Interrupted before evidence seal: ' + error)
        report = finalize(rows, out, seals, manifest_identity, args.output_mib * MIB, lambda: None, error, deadline)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    report.update(seconds=time.monotonic() - started, limitations=LIMITATIONS)
    remaining_metadata = RUN_RESERVE - (out / 'manifest.json').stat().st_size
    write_new(out / 'summary.json', report, remaining_metadata)
    print(f"{report['status']}: {report['complete']}/128 complete; integrity={report['integrity']} -> {out}")
    return 0 if report['status'] == 'complete' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    for name in MODELS:
        parser.add_argument('--' + name, required=True)
        parser.add_argument('--' + name + '-sha256', default=MODEL_SHA256[name], choices=[MODEL_SHA256[name]])
    parser.add_argument('--out', required=True, help='New output directory; relative to --root')
    parser.add_argument('--cpus', type=int, default=4, help='Affinity ceiling; sequential jobs and one torch thread')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--total-timeout', type=float, default=1800)
    parser.add_argument('--output-mib', type=int, default=64, help='Full per-game evidence cap, including result.json')
    args = parser.parse_args()
    try:
        return execute(args)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
