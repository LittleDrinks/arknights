"""Linux pool cleanup, including orphan Node groups after a worker crash."""
import ctypes
import multiprocessing
from multiprocessing import resource_tracker
import os
from pathlib import Path
import signal

from contracts_wide import require


def adopt_orphans():
    # Linux PR_SET_CHILD_SUBREAPER. P.Protocol deliberately starts Node in its
    # own session, so killing only a Python worker's group is insufficient.
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.prctl(36, 1, 0, 0, 0) == 0, 'Cannot enable child subreaper')


def descendants():
    table = {}
    for entry in Path('/proc').glob('[0-9]*/stat'):
        try:
            data = entry.read_text().rsplit(')', 1)[1].split()
            table[int(entry.parent.name)] = (int(data[1]), int(data[2]))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    owned = {os.getpid()}
    while True:
        found = {pid for pid, (parent, _) in table.items() if parent in owned} - owned
        if not found:
            return {pid: table[pid][1] for pid in owned if pid != os.getpid()}
        owned |= found


def kill_owned_nodes(exclude):
    killed = []
    for pid, group in descendants().items():
        if pid in exclude:
            continue
        try:
            if group == pid:
                os.killpg(group, signal.SIGKILL)
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            pass
    return killed


def shutdown(executor, hard=False, protected=()):
    workers = {p.pid: p for p in multiprocessing.active_children() if p.pid not in protected}
    # Only called by the CLI which owns this pool. Resource tracker and any
    # preexisting process are excluded; adopted Node processes are not.
    def terminate():
        kill_owned_nodes(set(protected) | set(workers))
        for child in workers.values():
            if child.is_alive():
                child.kill()
    if hard:
        terminate()
    interrupted = None
    try:
        executor.shutdown(wait=True, cancel_futures=True)
    except BaseException as exc:
        interrupted = exc
        terminate()
        executor.shutdown(wait=True, cancel_futures=True)
    killed = kill_owned_nodes(set(protected))
    for pid in killed:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    if interrupted is not None:
        raise interrupted
    return killed


def infrastructure_pids():
    # Capture the spawn resource tracker BEFORE workers start. Do not kill it.
    resource_tracker.ensure_running()
    return set(descendants())
