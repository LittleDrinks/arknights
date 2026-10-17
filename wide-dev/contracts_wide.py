"""Read-only reuse, pinned before imports; identities include lexical ancestors."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
CODE = HERE.parent
BASE = 'ffbe8cc6b994f0676cb386b7c9f88d9e8281333f'
MIB = 1024 ** 2
TOTAL_BYTES = 64 * 1024 ** 3
META_BYTES = 32 * MIB
MODELS = ('parent', 'steps3000')
LIMITATIONS = [
    'Predeclared expanded development seeds 1..256; NOT validation or 256 unseen seeds: early probes may overlap.',
    '2048 scheduled games share 256 paired seed groups, not 2048 independent observations.',
    '90017..90048 and 100001 onward remain unused; known original/mixed families only.',
    'Only the explicitly supplied completed steps3000 candidate; no checkpoint selection from dev results.',
    'Team and individual wins differ; failed/stopped/unrun rows remain in scheduled denominators.',
    'Collector semantics retain the F inputReviewStatus; hashes do not grant independent source approval.',
    'Production execution has not been tested by this implementation task.',
]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe(path):
    path = Path(path).absolute()
    require('..' not in path.parts, 'Path traversal forbidden')
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'Symlink forbidden: ' + str(part))
    return path


def digest(path, deadline=float('inf'), limit=64 * MIB):
    path = safe(path)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'Regular bounded file required: ' + str(path))
    h = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while True:
            require(time.monotonic() < deadline, 'Hash deadline')
            chunk = stream.read(65536)
            if not chunk:
                break
            size += len(chunk)
            require(size <= limit, 'Growing file exceeded budget')
            h.update(chunk)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, size, after.st_mtime_ns, after.st_ctime_ns), 'File changed during hashing')
    return dict(bytes=size, sha256=h.hexdigest())


def tree(path):
    path = safe(path)
    require(path.is_dir(), 'Directory required: ' + str(path))
    files = []
    def fail(error):
        raise error
    for parent, dirs, names in os.walk(path, followlinks=False, onerror=fail):
        for name in dirs + names:
            item = safe(Path(parent) / name)
            require(item.is_dir() if name in dirs else item.is_file(), 'Unexpected filesystem object')
        files.extend(Path(parent) / name for name in names)
    return sorted(files)


PINS = json.loads(safe(HERE / 'pins.json').read_bytes())


def dependency_guard():
    require(PINS['base'] == BASE, 'Wrong wide-dev base')
    for folder in PINS['trees']:
        actual = {p.relative_to(CODE).as_posix() for p in tree(CODE / folder)}
        require(actual == {n for n in PINS['files'] if n.startswith(folder + '/')}, 'Source file set: ' + folder)
    for name, sha in PINS['files'].items():
        require(digest(CODE / name)['sha256'] == sha, 'Pinned source changed: ' + name)


# No subprocess/model/game import before the manifest. Original helper modules
# are imported only after checking their complete source trees.
dependency_guard()
_search = sys.path[:]
sys.path.insert(0, str(CODE / 'bc-fit-budget'))
import contracts_fit as F
sys.path[:] = _search
P = F.G.load('wide_original_prep', CODE / 'prep-timing/run.py')
FT = F.G.load('wide_fit_training_helpers', CODE / 'bc-fit-budget/training.py')
C = F.C
require(tuple(P.VERSIONS) == ('off', 'deferred') and tuple(C.TEAMS) == ('original', 'mixed'), 'Fixed controllers/pools')


class Guard:
    """Remember file content/identity and every directory inode, without resolving links."""
    def __init__(self):
        self.nodes, self.files, self.trees, self.directories = {}, {}, {}, {}

    def path(self, path):
        path = safe(path)
        for part in (path, *path.parents):
            info = part.stat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'Regular file/directory only')
            value = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
            require(self.nodes.setdefault(str(part), value) == value, 'Directory/file inode changed: ' + str(part))
        return path

    def file(self, path):
        path = self.path(path)
        value = digest(path)
        require(self.files.setdefault(str(path), value) == value, 'File identity changed: ' + str(path))
        return value

    def directory(self, path):
        path = self.path(path)
        paths = tree(path)
        names = [p.relative_to(path).as_posix() for p in paths]
        require(self.trees.setdefault(str(path), names) == names, 'Directory file set changed')
        # Empty directories are identities too.
        directory_names = []
        for parent, dirs, _ in os.walk(path):
            self.path(parent)
            for name in dirs:
                child = self.path(Path(parent) / name)
                directory_names.append(child.relative_to(path).as_posix())
        require(self.directories.setdefault(str(path), sorted(directory_names)) == sorted(directory_names),
                'Subdirectory set changed')
        for item in paths:
            self.file(item)

    def verify(self, deadline):
        for path in self.nodes:
            self.path(path)
        for path, expected in self.files.items():
            require(digest(path, deadline) == expected, 'File content drift: ' + path)
        for path, names in self.trees.items():
            require([p.relative_to(path).as_posix() for p in tree(path)] == names, 'Directory file set drift: ' + path)
            dirs = sorted((Path(parent) / name).relative_to(path).as_posix()
                          for parent, children, _ in os.walk(path) for name in children)
            require(dirs == self.directories[path], 'Subdirectory set drift: ' + path)


def source_identity(code):
    require(safe(code) == CODE, '--experiment-code must be the actual CLI checkout')
    dependency_guard()
    safe(code / '.git')
    head = subprocess.check_output(['git', '-C', str(code), 'rev-parse', 'HEAD'], text=True, timeout=5).strip()
    subprocess.run(['git', '-C', str(code), 'merge-base', '--is-ancestor', BASE, head], check=True, timeout=5)
    own = {p.relative_to(HERE).as_posix(): digest(p) for p in tree(HERE)}
    return dict(base=BASE, codeCommit=head, dependencies=PINS, implementation=own,
                fit=F.source_identity(code), prep=P.source_identity())


def write_new(path, value, limit):
    raw = C.encoded(value)
    require(len(raw) <= limit, 'Metadata budget')
    with safe(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return dict(bytes=len(raw), sha256=C.digest(raw))
