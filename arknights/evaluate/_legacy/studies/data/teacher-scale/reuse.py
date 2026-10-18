"""Read-only pinned reuse; no engine/model imports or old frozen-plan entrypoint."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CODE = HERE.parent
PINS = json.loads((HERE / 'pins.json').read_bytes())
for name, expected in PINS['files'].items():
    if hashlib.sha256((CODE / name).read_bytes()).hexdigest() != expected:
        raise ValueError('Pinned dependency changed: ' + name)
sys.path.insert(0, str(CODE / 'natural-win-bc'))
import support as N
import admission as A
sys.path.insert(0, str(HERE))  # support inserts its dependency directory; keep private modules first
C, E = N.C, N.E
MIB = 1024 ** 2
REQUEST_BYTES = 64 * MIB
DISK_BYTES = 64 * 1024 * MIB
WORK_SECONDS = 3600
CLEANUP_SECONDS = 1800  # Full inline semantic audit; shared by manifest and absolute supervisor.


def plan():
    return [dict(index=i, seed=s, pool=p, lane=f'{p}-{(s-32001) % 16:02}', status='not-run')
            for i, (s, p) in enumerate((s, p) for s in range(32001, 36097) for p in ('normal', 'mixed'))]


def source_identity():
    for name, expected in PINS['files'].items():
        C.require(C.sha(CODE / name) == expected, 'Source changed: ' + name)
    actual = {str(p.relative_to(CODE)) for folder in ('rl', 'coop')
              for p in (CODE / folder).iterdir() if p.suffix in ('.py', '.mjs', '.json') and p.is_file()}
    C.require(actual == {n for n in PINS['files'] if n.startswith(('rl/', 'coop/'))}, 'Source file set changed')
    own = {p.name: C.sha(p) for p in HERE.iterdir() if p.is_file()}
    C.require(not any(p.is_symlink() for p in HERE.iterdir()), 'Collector symlink')
    return dict(base=PINS['base'], dependencies=PINS['files'], collector=own,
                referenceCollector=PINS['referenceCollector'])


def game_identity(root):
    pin = C.parse((CODE / 'policy/pins/rules-manifest.json').read_bytes())
    C.require(len(pin['files']) == 486 and pin['rulesCommit'] == C.RULES, '486 pinned rules/package files required')
    candidates = [p for p in (root / 'game', root / 'Stronghold-Protocol') if (p / 'server/match/Match.js').is_file()]
    C.require(len(candidates) == 1, 'Exactly one pinned game checkout required')
    game = candidates[0]
    actual = {str(p.relative_to(game)) for folder in ('server', 'shared', 'data')
              for p in (game / folder).rglob('*') if p.is_file() and p.suffix in ('.js', '.mjs', '.json')}
    actual.update(('package.json', 'package-lock.json'))
    C.require(actual == set(pin['files']), 'Game source/package file set changed')
    for name, sha in pin['files'].items():
        C.require(C.sha(game / name) == sha, 'Game source/package changed: ' + name)
    C.require(static_command(['git', '-C', str(game), 'rev-parse', 'HEAD'], 5).decode().strip() == C.RULES,
              'Game HEAD differs')
    # Same pinned static schema/data loader as C.game_identity, with bounded reads
    # and a private process group even for pre/post checks (no Match/Battle).
    identity = C.parse(static_command(['node', str(CODE / 'survivor-study/static.mjs'), str(game), str(CODE)], 10))
    C.require(C.dimensions(identity['schema']) == [5267, 419], 'Static schema dimensions')
    return game, {**identity, 'rules': C.RULES, 'treeSHA256': pin['treeSha256'], 'files': pin['files']}


def guard_wait(process, timeout):
    # SIGALRM must not land between waitpid reaping and Popen recording returncode;
    # the surrounding finally would otherwise mistake a reused PID for ownership.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    try:
        return process.wait(timeout=timeout)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def static_command(command, seconds):
    """Bounded guard subprocess, including all descendant cleanup on timeout/error."""
    selector, process = selectors.DefaultSelector(), None
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic()+seconds
    try:
        # The main-thread alarm must not interrupt Popen before its returned child
        # is assigned: this guard is not registered with the lane registry.
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=C.clean_env(), start_new_session=True, bufsize=0)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        selector.register(process.stdout, selectors.EVENT_READ, output)
        selector.register(process.stderr, selectors.EVENT_READ, errors)
        while selector.get_map():
            C.require(time.monotonic() < deadline, 'Static guard deadline')
            for key, _ in selector.select(max(0, deadline-time.monotonic())):
                remaining = 1_000_000-len(output)-len(errors)
                C.require(remaining > 0, 'Static guard byte limit')
                chunk = os.read(key.fd, min(65536, remaining))
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    key.data.extend(chunk)
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            C.require(time.monotonic() < deadline, 'Static guard exit deadline')
            time.sleep(min(.01, max(0, deadline-time.monotonic())))
        # Keep the exited leader unreaped until its private group is cleared.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        C.require(guard_wait(process, max(.001, deadline-time.monotonic())) == 0 and not errors,
                  'Static guard exit/stderr: ' + errors.decode(errors='replace'))
        return bytes(output)
    finally:
        if process:
            try:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                guard_wait(process, 2)
            finally:
                process.stdout.close()
                process.stderr.close()
        selector.close()


def outcome(data, row, game, pool):
    C.require(isinstance(data, dict) and data.get('ok') is True, 'Full response retained: engine rejected demo')
    t = data['terminal']
    p = next(p for p in t['summary']['players'] if p['playerId'] == 'learner')
    fields = dict(teamVictory=t['victory'], learnerVictory=p['victory'], learnerAlive=p['alive'],
                  rounds=t['rounds'], learnerRounds=p['roundsPassed'], rejectedLabels=data['rejectedLabels'],
                  samples=len(data['samples']), eligiblePositive=bool(t['victory'] and p['victory'] and
                  p['alive'] and p['roundsPassed'] == 14 and data['rejectedLabels'] == 0))
    A.complete_contract(data, {**row, **fields}, game, pool)
    return fields
