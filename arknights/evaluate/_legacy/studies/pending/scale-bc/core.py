"""Pinned reuse and bounded, read-only identities. No engine or learner execution."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
CODE = HERE.parent
BASE = '04928de6615d7ebd7c53351971faf3657524c981'
OLD_SHA = 'dc8af9e7a99f96b2ec08dab3992c7512745ad84697770d632a904cbf7fb0463b'
PARENT_SHA = 'f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a'
REFERENCE_SHA = 'af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b'
REFERENCE_REPORT_SHA = '8a7e6c600066c8b6fb7c8606375f217961514141396d3b24260cea3df073e2ff'
MIB = 1024**2
CONFIG = dict(epochs=15, drawsPerEpoch=6800, batchSize=34, lr=.0003,
              orderSeed=101, updates=3000, actualDraws=102000, optimizer='Adam', maxGradNorm=.5)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe(path):
    path = Path(path).absolute()
    require('..' not in path.parts, 'Path traversal')
    for p in (path, *path.parents):
        require(not p.is_symlink(), 'Symlink: ' + str(p))
    return path


def file_id(path, deadline=None, limit=64*1024*MIB):
    path = safe(path)
    s = path.stat()
    require(stat.S_ISREG(s.st_mode) and s.st_size <= limit, 'Regular bounded file: ' + str(path))
    h, size = hashlib.sha256(), 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Identity deadline')
            block = stream.read(min(MIB, limit-size+1))
            if not block:
                break
            size += len(block)
            require(size <= limit, 'File grew beyond bound')
            h.update(block)
    require(size == s.st_size, 'File size drift while reading')
    return dict(bytes=size, sha256=h.hexdigest())


def tree_paths(directory):
    directory = safe(directory)
    require(directory.is_dir(), 'Input directory missing')
    result = {}
    for root, dirs, files in os.walk(directory, followlinks=False):
        for name in sorted(dirs + files):
            path = safe(Path(root) / name)
            mode = path.stat().st_mode
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), 'Special input file')
            result[str(path.relative_to(directory))] = 'directory' if stat.S_ISDIR(mode) else 'file'
            require(len(result) <= 100000, 'File inventory bound')
    return result


def inventory(directory, deadline, limit=64*1024*MIB):
    paths, files, used = tree_paths(directory), {}, 0
    for name, kind in sorted(paths.items()):
        if kind == 'file':
            files[name] = file_id(directory / name, deadline, limit-used)
            used += files[name]['bytes']
    return dict(paths=paths, files=files)


PINS = json.loads(safe(HERE / 'pins.json').read_bytes())
require(PINS['base'] == BASE, 'Wrong base pin')
for name, expected in PINS['files'].items():
    require(file_id(CODE / name)['sha256'] == expected, 'Pinned dependency changed: ' + name)

# Existing modules have absolute sibling imports. Use their original import environment,
# restore sys.path afterwards, and never replace any dependency globals.
_search = sys.path[:]
sys.path.insert(0, str(CODE / 'teacher-scale'))
import reuse as S
import reconcile as R
sys.path[:] = _search
C, A, N = S.C, S.A, S.N


def load_fit():
    search = sys.path[:]
    try:
        sys.path.insert(0, str(CODE / 'bc-fit-budget'))
        spec = importlib.util.spec_from_file_location('scale_original_fit', CODE / 'bc-fit-budget/training.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = search


def read(path, limit=64*MIB):
    path = safe(path)
    require(path.is_file() and path.stat().st_size <= limit, 'Bounded JSON input')
    with path.open('rb') as f:
        raw = f.read(limit+1)
    require(len(raw) <= limit, 'JSON grew')
    return raw


def source_identity():
    for folder, expected in PINS['sets'].items():
        require(tree_paths(CODE / folder) == expected, 'Dependency file set drift: ' + folder)
    for name, expected in PINS['files'].items():
        require(file_id(CODE / name)['sha256'] == expected, 'Dependency drift: ' + name)
    own = inventory(HERE, time.monotonic()+30, 5*MIB)
    return dict(base=BASE, dependencies=PINS['files'], module=own)


def game_identity(root):
    rules = C.parse(read(CODE / 'policy/pins/rules-manifest.json'))
    for name in ('game', 'Stronghold-Protocol'):
        path = safe(root / name)
        if path.exists():
            for folder in ('server', 'shared', 'data'):
                tree_paths(path / folder)
            for filename in rules['files']:
                safe(path / filename)
    return S.game_identity(root)


def parent_path(root):
    path = safe(root / 'experiments/bc-expansion-v1/all.pt')
    require(file_id(path, limit=32*MIB)['sha256'] == PARENT_SHA, 'Original parent SHA')
    return path


class Evidence:
    """Exclusive flat output through a retained directory fd; aggregate byte accounting."""
    def __init__(self, out, limit):
        self.out, self.limit, self.used, self.files = safe(out), limit, 0, {}
        require(self.out.parent.is_dir(), 'Output parent must exist')
        self.out.mkdir(exist_ok=False)
        self.fd = os.open(self.out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.seal = os.fstat(self.fd)

    def guard(self):
        current = safe(self.out).stat()
        require((current.st_dev, current.st_ino) == (self.seal.st_dev, self.seal.st_ino), 'Output directory replaced')

    def put(self, name, data):
        self.guard()
        require(Path(name).name == name and name not in ('.', '..'), 'Flat output name')
        require(self.used + len(data) <= self.limit, 'Output budget')
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        self.used += len(data)
        self.files[name] = dict(bytes=len(data), sha256=C.digest(data))
        return self.files[name]

    def json(self, name, value, compress=False):
        raw = C.encoded(value)
        return self.put(name, gzip.compress(raw, mtime=0) if compress else raw)

    def verify(self):
        self.guard()
        require(tree_paths(self.out) == {n: 'file' for n in self.files}, 'Output file set drift')
        for name, expected in self.files.items():
            require(file_id(self.out / name) == expected, 'Output drift: ' + name)

    def close(self):
        os.close(self.fd)


class PrefixEvidence:
    def __init__(self, evidence, prefix):
        self.evidence, self.prefix = evidence, prefix

    def put(self, name, raw):
        return self.evidence.put(self.prefix + name, raw)

    def json(self, name, value, compress=False):
        return self.evidence.json(self.prefix + name, value, compress)
