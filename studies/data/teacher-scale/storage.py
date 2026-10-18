"""Shared compressed-byte budget, durable exclusive journals and atomic snapshots."""
import gzip
import hashlib
import os
from pathlib import Path
import threading
import time

from reuse import C, DISK_BYTES, MIB


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def new_output(path):
    path = Path(os.path.abspath(path))
    for p in (path, *path.parents):
        C.require(not p.is_symlink(), 'Output symlink component: ' + str(p))
    C.require(path.parent.is_dir(), 'Output parent must already exist')
    path.mkdir(exist_ok=False)
    sync_dir(path.parent)
    return path


class Budget:
    def __init__(self, limit=DISK_BYTES, reserve=256*MIB):
        self.limit, self.reserve, self.used, self.peak = limit, reserve, 0, 0
        self.lock = threading.Lock()

    def charge(self, n, metadata=False):
        with self.lock:
            C.require(self.used + n <= self.limit - (0 if metadata else self.reserve), 'Global disk budget')
            self.used += n
            self.peak = max(self.peak, self.used)

    def release(self, n):
        with self.lock:
            self.used -= n


class Sink:
    """Budget includes headers/trailers, stderr, commands, stages and snapshot overlap."""
    def __init__(self, path, budget, metadata=False, existing=None):
        self.path, self.budget, self.metadata = path, budget, metadata
        self.file = existing if existing is not None else path.open('xb', buffering=0)
        self.n = 0

    def write(self, data):
        self.budget.charge(len(data), self.metadata)
        view = memoryview(data)
        try:
            while view:
                n = self.file.write(view)
                if not n:
                    raise OSError('Short disk write')
                self.n += n
                view = view[n:]
        except BaseException:
            self.budget.release(len(view))
            raise
        return len(data)

    def flush(self):
        self.file.flush()

    def sync(self):
        self.flush()
        os.fsync(self.file.fileno())

    def close(self):
        if not self.file.closed:
            try:
                self.sync()
            finally:
                self.file.close()


class Compressed:
    def __init__(self, path, budget):
        self.sink = Sink(path, budget)
        try:
            self.gz = gzip.GzipFile(filename='', fileobj=self.sink, mode='wb', compresslevel=3, mtime=0)
        except BaseException:
            self.sink.close()
            raise
        self.n = 0

    def write(self, data):
        self.gz.write(data)
        self.n += len(data)
        return len(data)

    def sync(self):
        self.gz.flush()
        self.sink.sync()

    def close(self):
        try:
            self.gz.close()
        finally:
            self.sink.close()


def identity(path, deadline=None):
    h = hashlib.sha256()
    n = 0
    C.require(path.is_file() and not path.is_symlink(), 'Expected regular file: ' + str(path))
    with path.open('rb') as f:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Freeze deadline')
            chunk = f.read(1024*1024)
            if not chunk:
                break
            n += len(chunk)
            h.update(chunk)
    return dict(bytes=n, sha256=h.hexdigest())


def publish(path, value, budget, snapshot=False):
    """Stage, fsync, atomic publish, directory fsync; immutable records never overwrite."""
    stage = path.with_name(path.name + '.stage')
    stream = Sink(stage, budget, metadata=True)
    try:
        stream.write(C.encoded(value))
    finally:
        stream.close()
    old = path.stat().st_size if snapshot and path.exists() else 0
    if snapshot:
        os.replace(stage, path)
    else:
        os.link(stage, path)  # atomic O_EXCL semantics, unlike rename
        stage.unlink()
    sync_dir(path.parent)
    budget.release(old)
    return identity(path)


def case_raw(path, raw, budget):
    stage = path.with_name(path.name + '.stage')
    stream = Compressed(stage, budget)
    try:
        # Avoid an additional response-sized compressed allocation.
        for offset in range(0, len(raw), 65536):
            stream.write(memoryview(raw)[offset:offset+65536])
    finally:
        stream.close()
    os.link(stage, path)
    stage.unlink()
    sync_dir(path.parent)
    return {**identity(path), 'payloadBytes': len(raw), 'payloadSHA256': C.digest(raw),
            'jsonNewline': raw.endswith(b'\n'), 'gzipEOF': True}
