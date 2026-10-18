"""Persistent evaluation submissions and a read-only index of native run artifacts.

The viewer starts this loopback-only service. A background worker discovers
finished evaluations and prepares saved-command replays without a button click.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

from .process import run_command
from .runner import _safe_path, _write_json, sha_file


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path, default=None):
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return default


def latest_state(path):
    terminal = read(path / 'terminal.json')
    if terminal:
        return terminal
    protocol = path / 'protocol.jsonl'
    try:
        with protocol.open('rb') as stream:
            stream.seek(max(0, protocol.stat().st_size - 512 * 1024))
            lines = stream.read().splitlines()
        for line in reversed(lines):
            try:
                state = json.loads(line).get('state')
                if state:
                    return state
            except (ValueError, AttributeError):
                pass
    except OSError:
        pass
    return {}


def counts(games):
    return dict(planned=len(games), complete=sum(g.get('status') == 'complete' for g in games),
        running=sum(g.get('status') == 'running' for g in games),
        pending=sum(g.get('status') == 'pending' for g in games),
        failed=sum(g.get('status') in ('failed', 'cancelled') for g in games),
        teamWins=sum(g.get('teamVictory') is True for g in games),
        learnerWins=sum(g.get('learnerIndividualVictory') is True for g in games))


def record_process(directory, pid):
    directory.mkdir(exist_ok=True)
    birth = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    _write_json(directory / f'{pid}.json', {'pid': pid, 'birth': birth})


def cleanup_processes(directory):
    """Only our saved process groups; start time prevents killing a reused PID."""
    groups = []
    for p in directory.glob('*.json'):
        record = read(p, {})
        pid = record.get('pid')
        if type(pid) is not int or pid < 2:
            continue
        try:
            birth = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
            if birth != record.get('birth'):
                continue
        except FileNotFoundError:
            pass  # An exited group leader can still have its original children.
        try:
            os.killpg(pid, signal.SIGTERM)
            groups.append(pid)
        except ProcessLookupError:
            pass
    if groups:
        time.sleep(.2)
    for pid in groups:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Evaluations:
    def __init__(self, root):
        self.root = _safe_path(Path(root).absolute())
        self.directory = self.root / 'results/evaluations'
        (self.directory / 'jobs').mkdir(parents=True, exist_ok=True)
        (self.directory / 'replays').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.replays = ThreadPoolExecutor(max_workers=1, thread_name_prefix='saved-replay')
        self.cache_at, self.cached = 0., {}
        self.lease = (self.directory / '.dispatcher.lock').open('a')
        self.owner = False
        self._acquire()

    def _acquire(self):
        if self.owner:
            return True
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        self.owner = True
        # An interrupted subprocess is never silently resumed or counted complete.
        for p in (self.directory / 'jobs').glob('*.json'):
            job = read(p, {})
            if job.get('status') == 'running':
                cleanup_processes(self.directory / (job['id'] + '.processes'))
                job.update(status='failed', error='Queue service restarted during execution; inspect saved run', updatedAt=now())
                _write_json(p, job)
        for p in (self.directory / 'replays').glob('*.json'):
            r = read(p, {})
            if r.get('status') in ('queued', 'running'):
                cleanup_processes(self.directory / (p.stem + '.processes'))
                r.update(status='failed', error='Replay interrupted by service restart')
                _write_json(p, r)
        return True

    def roots(self):
        roots = [self.root]
        # Bounded discovery: project clones, never recursively walk training data.
        for pattern in ('results/workspaces/autonomous-*/*', 'results/workspaces/*'):
            for p in self.root.glob(pattern):
                if p.is_dir() and not p.is_symlink() and (p / 'arknights/adapter/backend.mjs').is_file():
                    roots.append(p)
        return list(dict.fromkeys(roots))

    def index(self, fresh=False):
        with self.lock:
            if not fresh and time.monotonic() - self.cache_at < 2:
                return self.cached
            jobs = {}
            linked = set()
            for p in (self.directory / 'jobs').glob('*.json'):
                j = read(p)
                if j:
                    jobs[j['id']] = j
                    if j.get('runPath'):
                        linked.add(j['runPath'])
            for root in self.roots():
                for p in (root / 'results/runs').glob('*/manifest.json'):
                    manifest = read(p, {})
                    spec = read(p.parent / 'spec.json', {})
                    if spec.get('schema') != 'arknights/evaluation-v2' or not isinstance(manifest.get('games'), list):
                        continue
                    run = p.parent
                    if str(run) in linked:
                        continue
                    key = 'run-' + hashlib.sha256(str(run).encode()).hexdigest()[:24]
                    stamp = datetime.fromtimestamp((run / 'spec.json').stat().st_mtime, timezone.utc).isoformat()
                    jobs[key] = dict(id=key, label=root.name + ' · ' + Path(spec.get('script', 'policy')).parent.name + '/' + Path(spec.get('script', 'policy')).name,
                        root=str(root), script=spec.get('script'), source='discovered', status=manifest.get('status', 'running'),
                        createdAt=stamp, updatedAt=datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
                        runId=run.name, runPath=str(run), error=manifest.get('guardError'))
            for j in jobs.values():
                manifest = read(Path(j['runPath']) / 'manifest.json', {}) if j.get('runPath') else {}
                games = manifest.get('games', [])
                j['progress'] = counts(games) if games else dict(planned=j.get('planned', 0), complete=0, running=0, pending=j.get('planned', 0), failed=0, teamWins=0, learnerWins=0)
                if j['status'] == 'running' and manifest.get('status') not in (None, 'running'):
                    j['status'] = manifest['status']
                if j['source'] == 'discovered' and j['status'] == 'running' and time.time() - Path(j['runPath'], 'manifest.json').stat().st_mtime > 600:
                    j['status'] = 'interrupted'
                    j['error'] = 'No progress for over ten minutes; run may have been interrupted'
            self.cached, self.cache_at = jobs, time.monotonic()
            return jobs

    def list(self):
        jobs = sorted(self.index().values(), key=lambda j: j['createdAt'], reverse=True)
        return {'jobs': jobs, 'queue': {'running': sum(j['status'] == 'running' for j in jobs),
                                      'waiting': sum(j['status'] == 'queued' for j in jobs)}}

    def detail(self, key):
        job = self.index().get(key)
        if not job:
            raise KeyError('Evaluation not found')
        result = dict(job)
        manifest = read(Path(job['runPath']) / 'manifest.json', {}) if job.get('runPath') else {}
        games = []
        for row in manifest.get('games', []):
            g = dict(row)
            if row.get('status') in ('running', 'complete', 'failed'):
                state = latest_state(Path(job['runPath']) / row['id'] / 'trajectory')
                own = state.get('own', {})
                g.update(round=state.get('round'), phase=state.get('phase'), lp=own.get('lp'),
                         funds=own.get('funds'), alive=own.get('alive'), bonds=own.get('bonds', []))
            g['replay'] = read(self.replay_file(key, row['id']), {'status': 'absent'})
            games.append(g)
        result['games'] = games
        return result

    def submit(self, request):
        from arknights.__main__ import seeds as parse_seeds
        from .runner import _plan
        raw = request.get('script', '')
        if not isinstance(raw, str) or not raw:
            raise ValueError('Choose a Python strategy script')
        script = _safe_path(self.root / raw)
        if not script.is_relative_to(self.root) or script.suffix != '.py' or not script.is_file():
            raise ValueError('Strategy must be a .py file inside this checkout')
        selected = parse_seeds(str(request.get('seeds', '1:8')))
        mode, difficulty = request.get('mode', 'coop'), request.get('difficulty', 'ABYSS')
        partners = request.get('partners', ['mixed']) if mode == 'coop' else ['original']
        if mode not in ('solo', 'coop') or difficulty not in ('NORMAL', 'HARD', 'ABYSS'):
            raise ValueError('Unsupported mode or difficulty')
        if not isinstance(partners, list) or not partners or len(set(partners)) != len(partners) or any(p not in ('original', 'mixed', 'weak') for p in partners):
            raise ValueError('Invalid partner profiles')
        cases = _plan(selected, partners)
        workers, timeout = request.get('workers', 2), request.get('timeout', 120)
        if type(workers) is not int or not 1 <= workers <= 8 or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 600 or len(cases) > 256:
            raise ValueError('Budget must be 1–8 workers, 1–600 seconds/case and at most 256 cases')
        key = uuid.uuid4().hex
        label = request.get('label') or script.relative_to(self.root).as_posix()
        if not isinstance(label, str) or len(label) > 160:
            raise ValueError('Label must contain at most 160 characters')
        job = dict(id=key, label=label, root=str(self.root), script=str(script), source='queue',
            status='queued', createdAt=now(), updatedAt=now(), error=None, planned=len(cases),
            config=dict(seeds=selected, mode=mode, difficulty=difficulty, partners=partners, workers=workers, timeout=timeout, max_requests=8192),
            # Capture neighboring policy imports too; reject changed source before dispatch.
            policyDirectory=str(script.parent),
            policySources={str(p): sha_file(_safe_path(p)) for p in script.parent.rglob('*.py') if '__pycache__' not in p.parts},
            runPath=None, runId=None)
        with self.lock:
            _write_json(self.directory / 'jobs' / (key + '.json'), job, exclusive=True)
            self.cache_at = 0
        return job

    def replay_file(self, key, episode):
        name = hashlib.sha256((key + '/' + episode).encode()).hexdigest()
        return self.directory / 'replays' / (name + '.json')

    def request_replay(self, key, episode):
        job = self.detail(key)
        row = next((r for r in job['games'] if r['id'] == episode), None)
        if not row or row['status'] != 'complete' or job['status'] in ('running', 'queued', 'interrupted'):
            raise ValueError('Choose a completed case after the run has finished')
        if read(Path(job['runPath']) / 'summary.json', {}).get('guardError'):
            raise ValueError('Run identity check failed; cannot publish replay')
        p = self.replay_file(key, episode)
        with self.lock:
            old = read(p, {})
            if old.get('status') in ('queued', 'running', 'ready'):
                return old
            value = {'status': 'queued', 'episode': episode, 'createdAt': now()}
            _write_json(p, value)
            self.replays.submit(self._replay, job, episode, p)
            return value

    def _replay(self, job, episode, status):
        value = {'status': 'running', 'episode': episode, 'updatedAt': now()}
        _write_json(status, value)
        token = status.stem + '-' + uuid.uuid4().hex[:8]
        try:
            from arknights.replay import _publish
            env = {**os.environ, 'PYTHONPATH': job['root']}
            # The backend inherits this owned group, so abrupt service recovery can reap it.
            entry = 'from arknights.evaluate import process; process._IN_WORKER=True; from arknights.__main__ import main; main()'
            result = run_command([sys.executable, '-c', entry, 'replay', job['runPath'], '--episode', episode,
                '--no-serve', '--timeout', '180'], cwd=job['root'], env=env, timeout=210,
                stdout_path=self.directory / (token + '.out'), stderr_path=self.directory / (token + '.err'), cancel=self.stop,
                on_start=lambda pid: record_process(self.directory / (status.stem + '.processes'), pid))
            if result.returncode or result.reason:
                raise RuntimeError((self.directory / (token + '.err')).read_text(errors='replace')[-1600:] or str(result.reason))
            report = read(self.directory / (token + '.out'))
            if not isinstance(report, dict):
                raise ValueError('Replay did not return a valid report')
            bundle = Path(report['path']) / 'bundle'
            if Path(job['root']) != self.root:
                identifier = _publish(self.root, bundle)
            else:
                identifier = report['episode']
            value.update(status='ready', url='/?replay=' + identifier, artifact=str(bundle))
        except Exception as exc:
            value.update(status='failed', error=str(exc))
        value['updatedAt'] = now()
        _write_json(status, value)

    def prepare_replays(self):
        """Prepare one case at a time, independently of evaluation execution."""
        if not self.owner:
            return
        if any(read(p, {}).get('status') in ('queued', 'running')
               for p in (self.directory / 'replays').glob('*.json')):
            return
        jobs = sorted(self.index().values(),
                      key=lambda j: (j['source'] == 'queue', j['createdAt']), reverse=True)
        for job in jobs:
            if job['status'] not in ('complete', 'failed') or not job.get('runPath'):
                continue
            manifest = read(Path(job['runPath']) / 'manifest.json', {})
            if manifest.get('guardError') or read(Path(job['runPath']) / 'summary.json', {}).get('guardError'):
                continue
            for row in manifest.get('games', []):
                if row.get('status') == 'complete' and not self.replay_file(job['id'], row['id']).exists():
                    self.request_replay(job['id'], row['id'])
                    return

    def prepare_loop(self):
        while not self.stop.wait(2):
            try:
                self.prepare_replays()
            except Exception as exc:
                print('Automatic replay: ' + str(exc), file=sys.stderr, flush=True)

    def dispatch(self):
        while not self.stop.wait(.5):
            if not self._acquire():
                continue
            queued = sorted((read(p, {}) for p in (self.directory / 'jobs').glob('*.json')), key=lambda j: j.get('createdAt', ''))
            job = next((j for j in queued if j.get('status') == 'queued'), None)
            if not job:
                continue
            p = self.directory / 'jobs' / (job['id'] + '.json')
            job.update(status='running', updatedAt=now())
            _write_json(p, job)
            self.cache_at = 0
            gate = self.directory / (job['id'] + '.start')
            try:
                gate.unlink(missing_ok=True)
                def started(pid):
                    record_process(self.directory / (job['id'] + '.processes'), pid)
                    gate.touch()
                maximum = job['config']['timeout'] * math.ceil(job['planned'] / job['config']['workers']) + 90
                result = run_command([sys.executable, '-m', 'arknights.evaluate.dashboard', 'work',
                    '--root', str(self.root), '--job', job['id']], cwd=self.root,
                    env={**os.environ, 'PYTHONPATH': str(self.root), 'ARK_EVAL_START_GATE': str(gate)}, timeout=maximum, cancel=self.stop,
                    on_start=started,
                    stdout_path=self.directory / (job['id'] + '.out'), stderr_path=self.directory / (job['id'] + '.err'))
                job = read(p, job)
                if result.returncode or result.reason:
                    cleanup_processes(self.directory / (job['id'] + '.processes'))
                    job.update(status='failed', error=(self.directory / (job['id'] + '.err')).read_text(errors='replace')[-1600:] or str(result.reason), updatedAt=now())
                    _write_json(p, job)
            except Exception as exc:
                cleanup_processes(self.directory / (job['id'] + '.processes'))
                job.update(status='failed', error=str(exc), updatedAt=now())
                _write_json(p, job)
            finally:
                gate.unlink(missing_ok=True)
            self.cache_at = 0

    def close(self):
        self.stop.set()
        self.replays.shutdown(wait=True, cancel_futures=True)
        self.lease.close()


def work(root, key):
    from .runner import evaluate
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    if os.environ.get('ARK_EVAL_START_GATE'):
        gate = Path(os.environ['ARK_EVAL_START_GATE'])
        deadline = time.monotonic() + 15
        while not gate.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Queue disappeared before process registration')
            time.sleep(.01)
    p = Path(root) / 'results/evaluations/jobs' / (key + '.json')
    job = read(p)
    sources = {str(path): sha_file(_safe_path(path)) for path in Path(job['policyDirectory']).rglob('*.py') if '__pycache__' not in path.parts}
    if sources != job['policySources']:
        raise ValueError('Queued policy source tree changed')
    def created(path):
        job.update(runId=path.name, runPath=str(path), updatedAt=now())
        _write_json(p, job)
    record_process(p.parent.parent / (key + '.processes'), os.getpid())
    run = evaluate(job['script'], root=root, **job['config'], _created=created,
        _started=lambda pid: record_process(p.parent.parent / (key + '.processes'), pid))
    job.update(status=run.summary['status'], updatedAt=now(), error=run.summary.get('guardError'))
    _write_json(p, job)


def serve(root, port):
    registry = Evaluations(root)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def response(self, status, data):
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def route(self):
            parts = [unquote(x) for x in urlsplit(self.path).path.split('/') if x]
            if parts[:2] != ['api', 'evaluations']:
                raise KeyError('Unknown route')
            if self.command == 'GET':
                return registry.list() if len(parts) == 2 else registry.detail(parts[2]) if len(parts) == 3 else self.missing()
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                raise ValueError('JSON body must be 1–16384 bytes')
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict):
                raise ValueError('JSON object required')
            if len(parts) == 2:
                return registry.submit(request)
            if len(parts) == 4 and parts[-1] == 'replay':
                return registry.request_replay(parts[2], request.get('episode', ''))
            raise KeyError('Unknown route')
        def missing(self):
            raise KeyError('Unknown route')
        def handle_request(self):
            try:
                self.response(200 if self.command == 'GET' else 202, self.route())
            except KeyError as exc:
                self.response(404, {'error': str(exc)})
            except (ValueError, TypeError, OSError) as exc:
                self.response(400, {'error': str(exc)})
            except Exception as exc:
                self.response(500, {'error': str(exc)})
        do_GET = do_POST = handle_request
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    dispatcher = threading.Thread(target=registry.dispatch, daemon=True)
    dispatcher.start()
    preparer = threading.Thread(target=registry.prepare_loop, daemon=True)
    preparer.start()
    def stop(*_):
        registry.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({'evaluationsPort': server.server_port}), flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        registry.close()
        dispatcher.join(timeout=10)
        preparer.join(timeout=10)
        server.server_close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('serve', 'work'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--port', type=int, default=0)
    p.add_argument('--job')
    args = p.parse_args()
    serve(args.root, args.port) if args.command == 'serve' else work(args.root, args.job)
