"""Bounded pipe I/O and process-group ownership for trusted research programs.

This is process isolation and accounting, not a sandbox. Children which deliberately
escape their process group are outside this trust contract.
"""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


MAX_BYTES = 64 * 1024 * 1024
# Worker backends inherit the supervisor's group, so abrupt worker death cannot
# strand a backend in a separately owned session.
_IN_WORKER = False


def _limits(timeout, max_bytes):
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('max_bytes must be a positive integer')


def _open(path):
    if path is None:
        return None
    path = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Output must not traverse a symlink')
    return path.open('xb')


def _signal(proc, sig, own_group):
    try:
        if own_group:
            os.killpg(proc.pid, sig)
        else:
            os.kill(proc.pid, sig)
    except ProcessLookupError:
        pass


def _stop(proc, own_group=True):
    # Always signal the group, even if its leader has already exited.
    _signal(proc, signal.SIGTERM, own_group)
    try:
        proc.wait(timeout=0.15)
    except subprocess.TimeoutExpired:
        pass
    _signal(proc, signal.SIGKILL, own_group)
    proc.wait(timeout=2)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    status: str
    reason: str | None
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stderr_bytes: int


def run_command(command, *, env=None, cwd=None, stdout_path=None,
                stderr_path=None, timeout=120, max_bytes=MAX_BYTES, cancel=None):
    """Supervise one fresh process group; drain both pipes under a total deadline.

    Saved/captured bytes are capped jointly. A byte-limit breach is a technical
    failure, never silent truncation. Cancellation is a threading.Event-like object.
    """
    _limits(timeout, max_bytes)
    files, proc, selector = [], None, selectors.DefaultSelector()
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    counts = {'stdout': 0, 'stderr': 0}
    reason = None
    try:
        for path in (stdout_path, stderr_path):
            files.append(_open(path))
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, bufsize=0)
        deadline = time.monotonic() + timeout
        for name, pipe in (('stdout', proc.stdout), ('stderr', proc.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        stopped = False
        while selector.get_map():
            if reason is None:
                if cancel is not None and cancel.is_set():
                    reason = 'cancelled'
                elif time.monotonic() >= deadline:
                    reason = 'timeout'
            if reason is not None and not stopped:
                _stop(proc)
                stopped = True
            # A successful leader may leave a descendant holding the pipes open.
            if proc.poll() is not None and not stopped:
                _stop(proc)
                stopped = True
            for key, _ in selector.select(0.05):
                try:
                    data = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                remaining = max(0, max_bytes - sum(counts.values()))
                kept = data[:remaining]
                name = key.data
                counts[name] += len(kept)
                output = files[0 if name == 'stdout' else 1]
                if output is None:
                    captured[name].extend(kept)
                else:
                    output.write(kept)
                if len(data) > remaining:
                    reason = reason or 'output_limit'
        # Children may close pipes while continuing to compute.
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                reason = reason or 'cancelled'
            if time.monotonic() >= deadline:
                reason = reason or 'timeout'
            if reason:
                _stop(proc)
                break
            try:
                proc.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        _stop(proc)
        reason = reason or (None if proc.returncode == 0 else 'nonzero_exit')
        return CommandResult(proc.returncode, 'cancelled' if reason == 'cancelled' else
                             ('failed' if reason else 'complete'), reason,
                             bytes(captured['stdout']), bytes(captured['stderr']),
                             counts['stdout'], counts['stderr'])
    finally:
        if proc is not None:
            _stop(proc)
            for pipe in (proc.stdout, proc.stderr):
                pipe.close()
        selector.close()
        for output in files:
            if output is not None:
                output.close()


class JsonlProcess:
    """Synchronous JSONL requests with nonblocking writes and stderr drainage.

    Raw stdout and stderr files, if requested, are created exclusively. The byte
    cap applies to the entire lifetime, not individually to each response.
    """

    def __init__(self, command, *, env=None, cwd=None, raw_path=None,
                 stderr_path=None, timeout=120, max_bytes=MAX_BYTES):
        _limits(timeout, max_bytes)
        self.timeout, self.max_bytes = timeout, max_bytes
        self.closed, self.eof = False, False
        self._failed = False
        self._count, self._buffer = 0, bytearray()
        self._raw = self._stderr = self.process = None
        self._selector = selectors.DefaultSelector()
        self._own_group = not _IN_WORKER
        try:
            self._raw = _open(raw_path)
            self._stderr = _open(stderr_path)
            self.process = subprocess.Popen(command, cwd=cwd, env=env,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=0,
                                            start_new_session=self._own_group)
            for name in ('stdin', 'stdout', 'stderr'):
                os.set_blocking(getattr(self.process, name).fileno(), False)
            for name in ('stdout', 'stderr'):
                self._selector.register(getattr(self.process, name), selectors.EVENT_READ, name)
        except BaseException:
            self._failed = True
            self.close()
            raise

    @property
    def returncode(self):
        return self.process.poll() if self.process is not None else None

    def _read(self, key):
        try:
            data = os.read(key.fd, 65536)
        except BlockingIOError:
            return
        if not data:
            self._selector.unregister(key.fileobj)
            if key.data == 'stdout':
                self.eof = True
            return
        remaining = max(0, self.max_bytes - self._count)
        kept = data[:remaining]
        self._count += len(kept)
        output = self._raw if key.data == 'stdout' else self._stderr
        if output is not None:
            output.write(kept)
            output.flush()
        if key.data == 'stdout':
            self._buffer.extend(kept)
        if len(data) > remaining:
            raise RuntimeError('Backend output_limit')

    def request(self, message):
        if self.closed:
            raise RuntimeError('Backend is closed')
        try:
            payload = (json.dumps(message, allow_nan=False, separators=(',', ':')) + '\n').encode()
            if len(payload) > self.max_bytes:
                raise ValueError('Request exceeds byte limit')
            if self._buffer:
                raise RuntimeError('Unsolicited backend output')
            self._selector.register(self.process.stdin, selectors.EVENT_WRITE, 'stdin')
            offset, deadline = 0, time.monotonic() + self.timeout
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Backend request timeout')
                for key, _ in self._selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    if key.data == 'stdin':
                        try:
                            offset += os.write(key.fd, payload[offset:offset + 65536])
                        except BlockingIOError:
                            continue
                        if offset == len(payload):
                            self._selector.unregister(key.fileobj)
                    else:
                        self._read(key)
                if b'\n' in self._buffer:
                    line, _, rest = self._buffer.partition(b'\n')
                    self._buffer = bytearray(rest)
                    if offset != len(payload) or rest:
                        raise RuntimeError('Unexpected backend response ordering')
                    response = json.loads(line)
                    if not isinstance(response, dict):
                        raise RuntimeError('Backend response must be an object')
                    return response
                if self.eof:
                    raise EOFError('Backend stdout closed before response')
        except BaseException:
            self._failed = True
            try:
                self.close()
            except Exception:
                pass  # Preserve the original request failure after cleanup.
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.process is not None:
                self.process.stdin.close()
                deadline = time.monotonic() + min(self.timeout, 0.3)
                while self._selector.get_map() and time.monotonic() < deadline:
                    for key, _ in self._selector.select(0.02):
                        if key.data == 'stdin':
                            self._selector.unregister(key.fileobj)
                        else:
                            self._read(key)
                try:
                    self.process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if self.process is not None:
                _stop(self.process, self._own_group)
                self.process.stdout.close()
                self.process.stderr.close()
            self._selector.close()
            for output in (self._raw, self._stderr):
                if output is not None:
                    output.close()
        if self.process is not None and not self._failed and self.process.returncode != 0:
            raise RuntimeError(f'Backend did not exit cleanly: {self.process.returncode}')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
