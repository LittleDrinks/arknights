"""Immutable evaluation plans, isolated cases, and independently verifiable runs.

Strategies are trusted research Python, not sandboxed code. V1 never resumes or
overwrites an attempt: repeat evaluations create a new run, including failures.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import uuid

from .process import run_command


RULES_COMMIT = '6ab2468279e926d107788f63d7fe219b85f67b4a'
SCHEMA = 'arknights/evaluation-v2'
LEGACY_SCHEMA = 'arknights/evaluation-v1'


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha_file(path):
    """Hash raw bytes incrementally; never normalize source or record environment."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _safe_path(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Symlink path is not allowed: ' + str(part))
    return path


def _write_json(path, value, *, exclusive=False):
    path = _safe_path(path)
    if exclusive:
        with path.open('xb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json(path):
    with _safe_path(path).open('rb') as stream:
        return json.load(stream)


def _git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            timeout=15, check=True)
    return result.stdout.decode().strip()


def _rules_identity(root, expected=None):
    game = _safe_path(root / 'game')
    commit = _git(game, 'rev-parse', 'HEAD')
    if commit != (expected or RULES_COMMIT):
        raise ValueError('Game rules commit differs from the fixed pristine rules')
    if _git(game, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('Game rules worktree is not pristine')
    return {'commit': commit, 'pristine': True}


def _code_identity(root):
    package = _safe_path(root / 'arknights')
    files = {}
    # Active Python/JS, browser transforms and reference data are dependencies;
    # archived legacy tools are disjoint. Reject symlinks in the active tree.
    for directory, dirs, names in os.walk(package, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ('__pycache__', '_legacy'))
        for name in dirs + sorted(names):
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError('Code identity cannot contain symlinks: ' + str(path))
            if path.is_file() and path.suffix in ('.py', '.mjs', '.js', '.json', '.css', '.html'):
                files[path.relative_to(root).as_posix()] = sha_file(path)
    if not files:
        raise ValueError('Missing arknights package source')
    return {'files': files, 'sha256': hashlib.sha256(encoded(files)).hexdigest()}


def _identity(root, script):
    script = _safe_path(script)
    if not script.is_file() or script.suffix != '.py':
        raise ValueError('Strategy must be an existing Python script')
    return {'script': {'path': str(script), 'sha256': sha_file(script)},
            'rules': _rules_identity(root), 'code': _code_identity(root)}


def check_identity(root, identity):
    """Verify the captured source, package dependencies, and pristine rules again."""
    script = _safe_path(identity['script']['path'])
    if sha_file(script) != identity['script']['sha256']:
        raise ValueError('Strategy source changed during evaluation')
    if _rules_identity(root, identity['rules']['commit']) != identity['rules']:
        raise ValueError('Rules identity changed during evaluation')
    if _code_identity(root) != identity['code']:
        raise ValueError('Package dependency source changed during evaluation')


def _inventory(directory, *, exclude=()):
    directory = _safe_path(directory)
    files = {}
    for parent, dirs, names in os.walk(directory, followlinks=False):
        for name in sorted(dirs) + sorted(names):
            path = _safe_path(Path(parent) / name)
            mode = path.stat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError('Non-regular artifact: ' + str(path))
            relative = path.relative_to(directory).as_posix()
            if relative not in exclude:
                files[relative] = {'sha256': sha_file(path), 'bytes': path.stat().st_size}
    return files


def _seal(directory):
    files = _inventory(directory, exclude=('seal.json',))
    _write_json(directory / 'seal.json', {'schema': SCHEMA, 'files': files}, exclusive=True)
    return files


def team_victory(state):
    if not isinstance(state, dict) or state.get('terminal') is not True:
        raise ValueError('Strategy did not reach a true terminal state')
    candidates = [state.get('teamVictory')]
    outcome = state.get('outcome')
    settlements = [state.get(name) for name in ('summary', 'result', 'outcome')]
    if isinstance(outcome, dict):
        settlements.extend((outcome.get('team'), outcome.get('summary')))
    for value in settlements:
        if isinstance(value, dict):
            if value.get('errors'):
                raise ValueError('Terminal game reported engine errors')
            candidates.extend((value.get('teamVictory'), value.get('victory')))
    values = [value for value in candidates if type(value) is bool]
    if not values or any(value != values[0] for value in values):
        raise ValueError('Terminal team victory is missing or inconsistent')
    return values[0]


def learner_victory(state):
    """Read individual victory explicitly, without inferring it from team success."""
    if not isinstance(state, dict) or state.get('terminal') is not True:
        raise ValueError('Learner outcome requires a true terminal state')
    outcome = state.get('outcome')
    learner = outcome.get('learner') if isinstance(outcome, dict) else None
    value = learner.get('victory') if isinstance(learner, dict) else None
    if type(value) is not bool:
        raise ValueError('Terminal learner individual victory must be an explicit boolean')
    return value


def _json_lines(path):
    with _safe_path(path).open('rb') as stream:
        for line in stream:
            if not line.endswith(b'\n'):
                raise ValueError('Truncated JSONL artifact: ' + str(path))
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError('Protocol records must be objects')
            yield value


def validate_case(directory, case, spec, *, result=None):
    """Check independent raw inventory and terminal, not just a worker assertion."""
    if result is None:
        result = _json(directory / 'result.json')
    if result.get('status') != 'complete' or result.get('id') != case['id']:
        raise ValueError('Missing complete worker result')
    state = result['state']
    victory = team_victory(state)
    if result.get('stateSHA256') != hashlib.sha256(encoded(state)).hexdigest():
        raise ValueError('Worker terminal state digest differs')
    if result.get('teamVictory') is not victory:
        raise ValueError('Worker victory differs from terminal')
    if spec.get('schema') != LEGACY_SCHEMA:
        if result.get('learnerIndividualVictory') is not learner_victory(state):
            raise ValueError('Worker learner individual victory differs from terminal')
    trajectory = directory / 'trajectory'
    if _json(trajectory / 'terminal.json') != state:
        raise ValueError('Saved terminal differs from worker state')
    # Inspect every request/response, including reset and the final observation.
    requests = iter(_json_lines(trajectory / 'requests.jsonl'))
    responses = iter(_json_lines(trajectory / 'protocol.jsonl'))
    count, last_state = 0, None
    for count, item in enumerate(requests, 1):
        if count > spec['max_requests'] or item.get('seq') != count:
            raise ValueError('Request sequence/budget differs')
        request = item.get('request')
        if not isinstance(request, dict):
            raise ValueError('Missing saved request')
        if count == 1:
            expected = {'cmd': 'reset', 'seed': case['seed'], 'mode': spec['mode'],
                        'difficulty': spec['difficulty'], 'partners': case['partner']}
            if any(request.get(key) != value for key, value in expected.items()):
                raise ValueError('Reset request differs from planned case')
        response = next(responses, None)
        if response is None or response.get('ok') is not True:
            raise ValueError('Missing or failed raw response')
        if 'state' in response:
            last_state = response['state']
    extra = next(responses, None)
    if extra is not None:
        if extra.get('ok') is not True or 'state' in extra or next(responses, None) is not None:
            raise ValueError('Unexpected extra raw responses')
    if not count or last_state != state or result.get('requests') != count:
        raise ValueError('Raw final state/request count differs')
    # Required even if empty, since stderr is part of the transport evidence.
    if not _safe_path(trajectory / 'stderr.log').is_file():
        raise ValueError('Missing backend stderr inventory')
    return result


class Run:
    def __init__(self, path):
        self.path = Path(path)

    @property
    def summary(self):
        return _json(self.path / 'summary.json')

    def replay(self, episode=None, **kwargs):
        verify_run(self.path)
        from arknights.replay import replay
        return replay(self, episode=episode, **kwargs)


def _plan(seeds, partners):
    seeds, partners = tuple(seeds), tuple(partners)
    if not seeds or not partners:
        raise ValueError('At least one seed and partner configuration are required')
    if any(type(seed) is not int or not 1 <= seed <= 4294967295 for seed in seeds):
        raise ValueError('Seeds must be integers in 1..4294967295')
    if any(100001 <= seed <= 100064 for seed in seeds):
        raise ValueError('Protected seeds 100001..100064 remain sealed; evaluation prohibited')
    if any(not isinstance(partner, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', partner)
           for partner in partners):
        raise ValueError('Partners must be nonempty safe configuration names')
    if len(set(seeds)) != len(seeds) or len(set(partners)) != len(partners):
        raise ValueError('Duplicate seeds or partners are not independent cases')
    return [{'id': f'{index:04d}-{partner}-s{seed}', 'seed': seed, 'partner': partner}
            for index, (partner, seed) in enumerate(
                ((partner, seed) for partner in partners for seed in seeds), 1)]


def _perform(root, path, case, spec, cancel, started=None):
    directory = _safe_path(path / case['id'])
    directory.mkdir(exist_ok=False)
    row = {**case, 'status': 'failed', 'teamVictory': None, 'learnerIndividualVictory': None}
    try:
        check_identity(root, spec['identity'])
        environment = dict(os.environ)
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        # An unused cache prefix plus -B also prevents importing stale dependency
        # bytecode from an existing __pycache__ in the shared checkout.
        environment['PYTHONPYCACHEPREFIX'] = str(directory / '.unused-bytecode')
        # Pin package resolution to this checkout, not arbitrary inherited paths.
        environment['PYTHONPATH'] = str(root)
        command = [sys.executable, '-B', '-m', 'arknights.evaluate.worker',
                   '--spec', str(path / 'spec.json'), '--case', case['id'],
                   '--script', spec['script'], '--root', str(root),
                   '--case-dir', str(directory)]
        execution = run_command(command, env=environment, cwd=root, timeout=spec['timeout'],
                                stdout_path=directory / 'stdout.log',
                                stderr_path=directory / 'stderr.log', cancel=cancel, on_start=started)
        row['process'] = {'returncode': execution.returncode, 'status': execution.status,
                          'reason': execution.reason, 'stdoutBytes': execution.stdout_bytes,
                          'stderrBytes': execution.stderr_bytes}
        if execution.status != 'complete':
            row.update(status=execution.status, error=execution.reason)
        else:
            result = validate_case(directory, case, spec)
            row.update(status='complete', teamVictory=result['teamVictory'],
                       learnerIndividualVictory=result['learnerIndividualVictory'],
                       stateSHA256=result['stateSHA256'])
        check_identity(root, spec['identity'])
    except Exception as exc:
        row.update(status='failed', teamVictory=None, learnerIndividualVictory=None,
                   error=f'{type(exc).__name__}: {exc}')
    # Never discard a failed worker result, traceback, or partial trajectory.
    _write_json(directory / 'assessment.json', row, exclusive=True)
    _seal(directory)
    return row


def _summary(rows, status, guard_error=None, *, schema=SCHEMA):
    total = len(rows)
    complete = sum(row['status'] == 'complete' for row in rows)
    wins = sum(row['status'] == 'complete' and row.get('teamVictory') is True for row in rows)
    failed = sum(row['status'] == 'failed' for row in rows)
    cancelled = sum(row['status'] == 'cancelled' for row in rows)
    summary = {'schema': schema, 'status': status, 'planned': total, 'total': total,
            'complete': complete, 'failed': failed, 'cancelled': cancelled,
            'technicalFailures': total - complete, 'teamWins': wins,
            'gameLosses': complete - wins, 'winRate': wins / total,
            'winRateDenominator': total, 'guardError': guard_error, 'games': rows}
    if schema != LEGACY_SCHEMA:
        learner_wins = sum(row['status'] == 'complete' and row.get('learnerIndividualVictory') is True
                           for row in rows)
        summary.update(learnerWins=learner_wins, learnerWinRate=learner_wins / total,
                       learnerWinRateDenominator=total, partial=0 < complete < total)
    return summary


def evaluate(script, *, root=None, seeds=tuple(range(1, 17)), mode='coop',
             difficulty='ABYSS', partners=('original',), timeout=120,
             workers=1, max_requests=4096, _created=None, _started=None):
    """Run each partner/seed once in its own Python subprocess, with no retries.

    Every planned case is in the denominator. Technical failures remain distinct
    from terminal game losses. Training and protected holdout seeds are not used.
    """
    root = _safe_path(root or Path(__file__).absolute().parents[2])
    script = _safe_path(Path(script) if Path(script).is_absolute() else root / script)
    if mode not in ('coop', 'solo') or difficulty not in ('NORMAL', 'HARD', 'ABYSS'):
        raise ValueError('Unsupported mode or difficulty')
    partners = ('original',) if mode == 'solo' else tuple(partners)
    if any(partner not in ('original', 'mixed', 'weak') for partner in partners):
        raise ValueError('Unsupported coop partner configuration; choose original, mixed, or weak')
    cases = _plan(seeds, partners)
    if type(workers) is not int or workers < 1:
        raise ValueError('workers must be a positive integer')
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError('max_requests must be a positive integer')
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    identity = _identity(root, script)
    runs = _safe_path(root / 'results' / 'runs')
    runs.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[^A-Za-z0-9_-]', '-', script.stem)[:64] or 'strategy'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = runs / f'{name}-{stamp}-{uuid.uuid4().hex[:12]}'
    path.mkdir(exist_ok=False)
    spec = {'schema': SCHEMA, 'run': path.name, 'root': str(root), 'script': str(script),
            'mode': mode, 'difficulty': difficulty, 'timeout': timeout, 'workers': workers,
            'max_requests': max_requests, 'cases': cases, 'identity': identity,
            'trust': 'trusted research code; subprocess isolation is not a sandbox',
            'resume': False}
    _write_json(path / 'identity.json', identity, exclusive=True)
    _write_json(path / 'spec.json', spec, exclusive=True)
    rows = [{**case, 'status': 'pending', 'teamVictory': None, 'learnerIndividualVictory': None}
            for case in cases]
    manifest = {'schema': SCHEMA, 'status': 'running', 'spec': 'spec.json',
                'identity': 'identity.json', 'games': rows}
    _write_json(path / 'manifest.json', manifest)
    if _created is not None:
        _created(path)
    cancelled, guard_error, cancel = False, None, threading.Event()
    executor = ThreadPoolExecutor(max_workers=min(workers, len(cases)))
    futures, next_case = {}, 0
    try:
        while next_case < len(cases) or futures:
            while not cancel.is_set() and next_case < len(cases) and len(futures) < workers:
                index = next_case
                rows[index]['status'] = 'running'
                _write_json(path / 'manifest.json', manifest)
                futures[executor.submit(_perform, root, path, cases[index], spec, cancel, _started)] = index
                next_case += 1
            if not futures:
                break
            done, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                index = futures.pop(future)
                try:
                    rows[index] = future.result()
                except Exception as exc:
                    rows[index].update(status='failed', error=f'{type(exc).__name__}: {exc}')
                _write_json(path / 'manifest.json', manifest)
    except KeyboardInterrupt:
        cancelled = True
        cancel.set()
    except Exception as exc:
        guard_error = f'{type(exc).__name__}: {exc}'
        cancel.set()
    finally:
        executor.shutdown(wait=True, cancel_futures=False)
        for future, index in futures.items():
            try:
                rows[index] = future.result()
            except Exception as exc:
                rows[index].update(status='failed', error=f'{type(exc).__name__}: {exc}')
        for row in rows:
            if row['status'] in ('pending', 'running'):
                row.update(status='cancelled', error='Run interrupted before case completion')
    try:
        check_identity(root, identity)
        if _json(path / 'spec.json') != spec or _json(path / 'identity.json') != identity:
            raise ValueError('Immutable run specification or identity changed')
    except Exception as exc:
        guard_error = f'{type(exc).__name__}: {exc}'
    if guard_error:
        for row in rows:
            if row['status'] == 'complete':
                row.update(status='failed', teamVictory=None, learnerIndividualVictory=None,
                           error='Run end guard failed: ' + guard_error)
    status = ('failed' if guard_error else 'cancelled' if cancelled else
              'complete' if all(row['status'] == 'complete' for row in rows) else
              'partial' if any(row['status'] == 'complete' for row in rows) else 'failed')
    manifest.update(status=status, guardError=guard_error)
    _write_json(path / 'manifest.json', manifest)
    _write_json(path / 'summary.json', _summary(rows, status, guard_error), exclusive=True)
    _seal(path)
    return Run(path)


def verify_run(path):
    """Read and verify all saved bytes before replay; no source re-execution.

    Hashes detect corruption and are not signatures against malicious rewriting.
    A failed run is inspectable, but guard failures invalidate it for replay.
    """
    path = _safe_path(path)
    seal = _json(path / 'seal.json')
    if seal.get('schema') not in (SCHEMA, LEGACY_SCHEMA) or seal.get('files') != _inventory(path, exclude=('seal.json',)):
        raise ValueError('Run artifact inventory/hash mismatch')
    spec, identity = _json(path / 'spec.json'), _json(path / 'identity.json')
    manifest, summary = _json(path / 'manifest.json'), _json(path / 'summary.json')
    schema = spec.get('schema')
    if spec.get('identity') != identity or schema != seal['schema']:
        raise ValueError('Run identity/specification mismatch')
    if summary.get('guardError') or manifest.get('guardError'):
        raise ValueError('Evaluation end guards failed; run cannot be replayed')
    rows = summary['games']
    if manifest.get('games') != rows or manifest.get('status') != summary.get('status'):
        raise ValueError('Manifest/summary differs')
    planned = spec['cases']
    if len(rows) != len(planned) or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Evaluation denominator/unique case inventory differs')
    for row, case in zip(rows, planned):
        if any(row.get(key) != case.get(key) for key in ('id', 'seed', 'partner')):
            raise ValueError('Case identity differs from plan')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', row['id']):
            raise ValueError('Invalid saved case path')
        if row['status'] not in ('complete', 'failed', 'cancelled'):
            raise ValueError('Unfinished saved evaluation')
        directory = path / row['id']
        if row['status'] == 'complete':
            process = row.get('process', {})
            if process.get('returncode') != 0 or process.get('status') != 'complete':
                raise ValueError('Complete case lacks clean worker exit')
            result = validate_case(directory, case, spec)
            if row.get('teamVictory') != result['teamVictory'] or row.get('stateSHA256') != result['stateSHA256']:
                raise ValueError('Case summary differs from sealed result')
            if schema != LEGACY_SCHEMA and row.get('learnerIndividualVictory') is not result['learnerIndividualVictory']:
                raise ValueError('Case learner individual victory differs from sealed result')
        elif schema != LEGACY_SCHEMA and (row.get('teamVictory') is not None or
                                          row.get('learnerIndividualVictory') is not None):
            raise ValueError('Incomplete case cannot claim a terminal victory outcome')
        if directory.exists():
            case_seal = _json(directory / 'seal.json')
            if case_seal.get('files') != _inventory(directory, exclude=('seal.json',)):
                raise ValueError('Case artifact inventory/hash mismatch')
    if _summary(rows, summary['status'], schema=schema) != summary:
        raise ValueError('Summary totals differ from all planned cases')
    if summary['status'] == 'complete' and summary['complete'] != len(rows):
        raise ValueError('Incomplete cases claimed as a complete run')
    return summary
