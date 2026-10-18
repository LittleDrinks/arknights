"""Durable per-case lifecycle. Main alone publishes manifest snapshots."""
from datetime import datetime, timezone
import queue
import threading
import time
import traceback

from reuse import C, REQUEST_BYTES, outcome
from storage import case_raw, identity, publish, sync_dir
from wire import LaneProtocol, Registry, WireBudget


def clock():
    return datetime.now(timezone.utc).isoformat()


def error_text(exc):
    return ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def base_row(row):
    return {k: row[k] for k in ('index', 'seed', 'pool', 'lane')}


def case_name(row):
    return f"{row['index']:04}-{row['pool']}-{row['seed']}"


def lane_task(rows, out, command, env, game, pool_hash, budget, wire, stop, registry,
              events, work_end, cleanup_end, request_seconds=120, byte_limit=REQUEST_BYTES):
    lane = rows[0]['lane']
    directory = out / 'lanes' / lane
    channel, errors, eof = None, [], False
    lane_start = dict(lane=lane, pool=rows[0]['pool'], status='started', startedAt=clock(), finishedAt=None)
    start_identity = None
    try:
        directory.mkdir()
        sync_dir(directory.parent)
        start_identity = publish(directory / 'started.json', lane_start, budget)
        if time.monotonic() >= work_end or stop.is_set():
            return
        channel = LaneProtocol(command, env, directory, min(work_end, time.monotonic()+request_seconds),
                               budget, wire, stop, registry, byte_limit)
        for planned in rows:
            if stop.is_set() or time.monotonic() >= work_end:
                break
            name = case_name(planned)
            case_begin = time.monotonic()
            case_end = min(work_end, case_begin+request_seconds)
            result = dict(**base_row(planned), status='started', startedAt=clock(), finishedAt=None,
                          stdoutStart=channel.stdout_bytes, stderrStart=channel.stderr_bytes)
            # This is durable BEFORE request intent or any demo bytes reach the child.
            started = publish(out / 'journals' / (name + '.started.json'), result, budget)
            events.put(('case', dict(result)))
            try:
                channel.begin(case_end)
                raw = channel.receive(dict(cmd='demo', seed=planned['seed'], mode='coop',
                                           difficulty='ABYSS', budget=128),
                                      dict(index=planned['index'], seed=planned['seed'], pool=planned['pool']))
                artifact = 'cases/' + name + '.json.gz'
                result.update(artifact=artifact, artifactIdentity=case_raw(out / artifact, raw, budget))
                # Parse ONLY after a bounded full raw frame is durably published.
                data = C.parse(raw)
                result['jsonEOF'] = True
                result.update(outcome(data, planned, game, pool_hash))
                C.require(not channel.buffer, 'Extra response bytes after case frame')
                C.require(channel.stderr_bytes == result['stderrStart'], 'Stderr during demo')
                result['status'] = 'complete'
            except BaseException as e:
                result.update(status='failed', error=error_text(e), eligiblePositive=False)
                errors.append('Case failed: ' + name)
                stop.set()
            finally:
                result.update(stdoutEnd=channel.stdout_bytes,
                              stderrEnd=channel.stderr_bytes, wireBytes=channel.request_bytes,
                              startedIdentity=started, processEOF=False)
                try:
                    channel.sync()  # durable gzip prefix + stderr/commands before completed journal
                    C.require(time.monotonic() <= case_end, 'Case processing/fsync exceeded request/work deadline')
                except BaseException as e:
                    result.update(status='failed', syncError=error_text(e), eligiblePositive=False)
                    errors.append('Case prefix fsync failed: ' + name)
                    stop.set()
                result.update(finishedAt=clock(), seconds=time.monotonic()-case_begin)
                publish(out / 'journals' / (name + '.completed.json'), result, budget)
                events.put(('case', dict(result)))
    except BaseException as e:
        errors.append(error_text(e))
        stop.set()
    finally:
        if channel:
            try:
                # Even a protocol/timeout failure gets a finite ACK attempt if time remains.
                # A partial frame makes begin fail; no invented EOF success in that case.
                channel.finish_lane(min(cleanup_end, time.monotonic()+5))
                eof = True
            except BaseException as e:
                errors.append(error_text(e))
                stop.set()
            try:
                channel.close()  # kill only while owning the unreaped private-group leader
            except BaseException as e:
                errors.append(error_text(e))
                stop.set()
        result = {**lane_start, 'status': 'failed' if errors else 'complete', 'finishedAt': clock(),
                  'closeACKAndProcessEOF': eof, 'spawned': channel is not None,
                  'startedIdentity': start_identity, 'errors': errors,
                  'wireBytes': channel.bytes if channel else 0,
                  'stdoutBytes': channel.stdout_bytes if channel else 0,
                  'stderrBytes': channel.stderr_bytes if channel else 0}
        try:
            result['files'] = {p.name: identity(p, cleanup_end) for p in sorted(directory.iterdir())
                               if p.name != 'started.json' and p.is_file()}
            publish(directory / 'completed.json', result, budget)
        except BaseException as e:
            result['errors'].append(error_text(e))
            result['status'] = 'failed'
            stop.set()
        events.put(('lane', result))


def run_lanes(out, rows, commands, game, pool_hash, budget, manifest, work_end, cleanup_end,
              stop, *, request_seconds=120, byte_limit=REQUEST_BYTES, registry=None):
    """Internal small fixtures use explicit rows; the production CLI has no plan/limit switches."""
    events, registry = queue.Queue(), registry or Registry()
    wire = WireBudget(len(rows)*byte_limit)
    lanes = sorted({r['lane'] for r in rows})
    threads = []
    registry.workers = threads
    for lane in lanes:
        members = [r for r in rows if r['lane'] == lane]
        command, env = commands[members[0]['pool']]
        thread = threading.Thread(target=lane_task, name=lane, daemon=True,
                                  args=(members, out, command, env, game, pool_hash, budget, wire, stop,
                                        registry, events, work_end, cleanup_end, request_seconds, byte_limit))
        threads.append(thread)
        thread.start()
    last_snapshot, killed = 0, False
    try:
        while any(t.is_alive() for t in threads) or not events.empty():
            now = time.monotonic()
            if now >= cleanup_end:
                stop.set()
                registry.kill()
                raise TimeoutError('Lane cleanup deadline; not frozen')
            if now >= work_end+8 and not killed:
                stop.set()
                manifest['globalErrors'].append('Forced cleanup after work deadline')
                manifest['globalErrors'].extend(registry.kill())
                killed = True
            try:
                kind, row = events.get(timeout=.1)
                if kind == 'case':
                    rows[row['index']] = row
                else:
                    manifest['lanes'][row['lane']] = row
                    if row['status'] != 'complete':
                        manifest['globalErrors'].append('Failed lane: ' + row['lane'])
            except queue.Empty:
                pass
            if now-last_snapshot >= 2:
                manifest.update(games=rows, wireBytes=wire.used, diskBytes=budget.used)
                publish(out / 'manifest.json', manifest, budget, snapshot=True)
                last_snapshot = now
        for thread in threads:
            thread.join(timeout=0)
    finally:
        if any(t.is_alive() for t in threads):
            stop.set()
        manifest['globalErrors'].extend(registry.kill())
        join_end = min(cleanup_end, time.monotonic()+3)
        for thread in threads:
            thread.join(timeout=max(0, join_end-time.monotonic()))
    manifest.update(wireBytes=wire.used, diskBytes=budget.used)
    return registry
