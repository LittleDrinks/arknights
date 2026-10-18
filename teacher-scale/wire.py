"""Private persistent E.Protocol subclass. Never alter reused module globals."""
import os
import signal
import threading
import time

from reuse import C, E, REQUEST_BYTES
from storage import Compressed, Sink, sync_dir


class WireBudget:
    def __init__(self, limit):
        self.limit, self.used = limit, 0
        self.lock = threading.RLock()

    def charge(self, n):
        with self.lock:
            C.require(self.used + n <= self.limit, 'Aggregate wire budget')
            self.used += n


class Registry:
    def __init__(self):
        self.lock, self.channels = threading.RLock(), []
        self.workers = []
        self.stopping = False

    def add(self, channel):
        with self.lock:
            C.require(not self.stopping, 'Process registry stopped before spawn')
            self.channels.append(channel)

    def release(self, channel):
        # Caller holds lock through wait and ownership release. Never poll/reap
        # to decide whether to signal a numeric PGID: that permits PID reuse.
        if channel in self.channels:
            self.channels.remove(channel)

    def kill_owned(self, channel):
        # Called only with lock held, before any wait that can reap the leader.
        process = channel.process
        if channel in self.channels and process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def kill(self):
        errors = []
        with self.lock:
            self.stopping = True
            for c in self.channels:
                try:
                    self.kill_owned(c)
                except OSError as e:
                    errors.append(str(e))
        return errors


class LaneProtocol(E.Protocol):
    def __init__(self, command, env, out, deadline, budget, wire, stop, registry, limit=REQUEST_BYTES):
        self.stop, self.wire, self.closing = stop, wire, False
        self.stdout_bytes, self.request_bytes = 0, 0
        self.request_limit = limit
        self.closed = False
        self.process = None
        self.raw = self.errors = self.actions = self.selector = None
        self.registry = registry
        # Registration, Popen assignment and exception cleanup are one ownership
        # transaction. E.__init__ may call our close(), hence the shared RLock.
        with registry.lock:
            try:
                registry.add(self)
                super().__init__(command, env, out, deadline, limit)
                self.raw.close()
                (out / 'protocol.jsonl').unlink()
                self.raw = Compressed(out / 'stdout.gz', budget)
                self.errors = Sink(out / 'stderr.log', budget, existing=self.errors)
                self.actions = Sink(out / 'actions.jsonl', budget, metadata=True, existing=self.actions)
                sync_dir(out)
            except BaseException:
                self.close()
                raise

    def begin(self, deadline):
        C.require(not self.buffer, 'Unsolicited/trailing protocol bytes before next request')
        self.deadline, self.request_bytes = deadline, 0

    def charge(self, n):
        # Delta accounting, never charge cumulative lane bytes as one request.
        C.require(self.request_bytes + n <= self.request_limit, 'Per-request 64MiB wire cap')
        self.wire.charge(n)
        self.request_bytes += n
        self.bytes += n

    def pump(self):
        # Original E.pump charges before storing a 64KiB read and has a 20MB line
        # cap. This override bounds the read itself and preserves every read byte.
        while True:
            if self.stop.is_set() and not self.closing:
                raise InterruptedError('Batch stopped')
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Request/work/cleanup deadline')
            events = self.selector.select(min(.1, remaining))
            if events:
                break
        for key, _ in events:
            with self.wire.lock:
                capacity = min(self.request_limit - self.request_bytes, self.wire.limit-self.wire.used)
                C.require(capacity > 0, 'Per-request/aggregate wire limit reached before EOF/frame')
                chunk = os.read(key.fd, min(65536, capacity))
                self.charge(len(chunk))
            if not chunk:
                self.selector.unregister(key.fileobj)
                continue
            if key.data == 'stdout':
                self.stdout_bytes += len(chunk)
                self.raw.write(chunk)
                self.buffer.extend(chunk)
            else:
                self.stderr_bytes += len(chunk)
                self.errors.write(chunk)

    def receive(self, command, record=None):
        C.require(time.monotonic() < self.deadline, 'Request deadline before send')
        self.actions.write(C.encoded(dict(command=command, **(record or {}))))
        self.actions.sync()  # independent durable request intent before child execution
        encoded = C.encoded(command)
        if self.stop.is_set() and not self.closing:
            raise InterruptedError('Batch stopped before send')
        C.require(time.monotonic() < self.deadline, 'Request deadline after intent fsync before send')
        self.process.stdin.write(encoded)
        while b'\n' not in self.buffer:
            C.require(any(k.data == 'stdout' for k in self.selector.get_map().values()), 'Unexpected protocol EOF')
            self.pump()
        end = self.buffer.index(b'\n') + 1
        raw = bytes(self.buffer[:end])
        del self.buffer[:end]
        return raw

    def request(self, command):
        raw = self.receive(command)
        return C.parse(raw), raw

    def sync(self):
        for stream in (self.raw, self.errors, self.actions):
            stream.sync()

    def finish_lane(self, deadline):
        self.closing = True
        self.begin(deadline)
        # Same ACK/EOF/exit contract as E.finish; reap under the ownership lock.
        ack, _ = self.request({'cmd': 'close'})
        C.require(ack == {'ok': True}, 'Close acknowledgement')
        self.process.stdin.close()
        while self.selector.get_map():
            self.pump()
        C.require(not self.buffer, 'Extra/truncated data after close acknowledgement')
        while True:
            remaining = self.deadline-time.monotonic()
            C.require(remaining > 0, 'EOF deadline')
            with self.registry.lock:
                C.require(self in self.registry.channels and self.process.returncode is None,
                          'Finish requires owned unreaped child')
                # Observe exit without releasing PID ownership. Kill remaining
                # descendants BEFORE reaping; never signal the old group afterward.
                exited = os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if exited is not None:
                    self.registry.kill_owned(self)
                    try:
                        code = self.process.wait(timeout=remaining)
                    finally:
                        if self.process.returncode is not None:
                            self.registry.release(self)
                    C.require(code == 0 and self.stderr_bytes == 0, 'Engine exit/stderr error')
                    return
            time.sleep(min(.01, remaining))

    def close(self):
        errors = []
        with self.registry.lock:
            if self.closed:
                return
            if self.process:
                try:
                    self.registry.kill_owned(self)
                except OSError as e:
                    errors.append(e)
                try:
                    self.process.wait(timeout=2)
                except BaseException as e:
                    errors.append(e)
                if self.process.returncode is not None:
                    self.registry.release(self)
                for f in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if f and not f.closed:
                        try:
                            f.close()
                        except OSError as e:
                            errors.append(e)
                if self.process.returncode is not None:
                    self.process = None
            else:
                self.registry.release(self)
            # A timed-out wait retains ownership for another safe cleanup attempt.
            self.closed = self.process is None
        if self.selector is not None:
            self.selector.close()
        for stream in (self.raw, self.errors, self.actions):
            try:
                if stream is not None:
                    stream.close()
            except BaseException as e:
                errors.append(e)
        if errors:
            raise RuntimeError('Lane cleanup failed: ' + '; '.join(str(e) for e in errors))
