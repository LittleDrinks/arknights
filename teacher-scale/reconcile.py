"""Independent disk reconciliation; never trust Queue results as execution inventory."""
import gzip
import hashlib
import time

from reuse import A, C, REQUEST_BYTES, DISK_BYTES
from storage import identity
from collector import base_row, case_name


def read_record(path):
    C.require(not path.is_symlink() and path.stat().st_size <= 2*1024**2, 'Journal bounds/symlink')
    with path.open('rb') as f:
        raw = f.read(2*1024**2+1)
    C.require(len(raw) <= 2*1024**2, 'Journal grew')
    return C.parse(raw)


def payload_identity(path, limit, deadline, *, retain=False):
    h, n, last = hashlib.sha256(), 0, b''
    payload = bytearray() if retain else None
    with gzip.open(path, 'rb') as f:
        while True:
            C.require(time.monotonic() < deadline, 'Freeze payload deadline')
            block = f.read(min(65536, limit-n+1))
            if not block:
                break
            n += len(block)
            C.require(n <= limit, 'Expanded raw limit')
            h.update(block)
            if retain:
                payload.extend(block)
            last = block[-1:]
    C.require(last == b'\n', 'Case JSON/newline EOF')
    return dict(payloadBytes=n, payloadSHA256=h.hexdigest(), jsonNewline=True, gzipEOF=True), payload


def reconcile(out, planned, deadline, *, byte_limit=REQUEST_BYTES):
    rows = [{**base_row(r), 'status': 'not-run'} for r in planned]
    errors, files, expected = [], {}, {'plan.json', 'source-before.json', 'source-after.json'}
    starts, dones, lanes, artifacts = {}, {}, {}, {}
    ambiguous = False
    source = {}

    def fail(message):
        errors.append(message)

    # First enumerate *all* evidence. A recognized artifact/stage already disproves not-run,
    # even if its started journal was lost. Unknown evidence makes untouched rows unknown.
    for p in sorted(out.rglob('*')):
        rel = str(p.relative_to(out))
        if p.is_symlink():
            fail('Symlink evidence: ' + rel)
            ambiguous = True
            continue
        if p.is_dir():
            if rel not in {'journals', 'cases', 'lanes'} | {'lanes/'+r['lane'] for r in planned}:
                fail('Unexpected directory: ' + rel)
                ambiguous = True
            continue
        if rel == 'manifest.json':
            continue
        if rel.startswith(('journals/', 'cases/')):
            prefix = p.name.split('-', 1)[0]
            if prefix.isdigit() and 0 <= int(prefix) < len(rows):
                rows[int(prefix)].update(status='interrupted', error='Evidence exists; awaiting reconciliation')
            else:
                ambiguous = True
        # SHA is independently recomputed from disk, including all stages/error wire.
        files[rel] = identity(p, deadline)

    try:
        schedule = read_record(out / 'plan.json')
        C.require(schedule == dict(games=planned, planned=len(planned), split='train'), 'Immutable plan mismatch')
        source = read_record(out / 'source-before.json')
        C.require(source == read_record(out / 'source-after.json'), 'Source drift')
    except Exception as e:
        fail('Plan/source evidence: ' + str(e))

    # Read request logs independently of the lane journals and their claimed hashes.
    # A missing lane-completed record must not hide already dispatched commands.
    for name in files:
        if not name.endswith('/actions.jsonl'):
            continue
        try:
            C.require(files[name]['bytes'] <= 2*1024**2, 'Request log bounds')
            for line in (out / name).read_bytes().splitlines():
                a = C.parse(line)
                if a.get('command') == {'cmd': 'close'}:
                    continue
                i = a.get('index')
                C.require(type(i) is int and 0 <= i < len(rows), 'Unattributable request')
                rows[i].update(status='interrupted', error='Request evidence exists')
        except Exception as e:
            fail('Request evidence: ' + str(e))
            ambiguous = True

    for r in rows:
        stem = case_name(r)
        start_name = 'journals/' + stem + '.started.json'
        done_name = 'journals/' + stem + '.completed.json'
        raw_name = 'cases/' + stem + '.json.gz'
        i = r['index']
        for name in (start_name, done_name, raw_name):
            if name in files:
                expected.add(name)
        try:
            if start_name in files:
                s = read_record(out / start_name)
                C.require(base_row(s) == base_row(r) and s['status'] == 'started' and
                          s['startedAt'] and s['finishedAt'] is None, 'Started identity/status/timestamps')
                starts[i] = s
                r.update(s, status='interrupted', error='Started without valid completed journal')
            if done_name in files:
                d = read_record(out / done_name)
                C.require(i in starts and base_row(d) == base_row(r) and
                          d['startedAt'] == starts[i]['startedAt'] and d['finishedAt'] and
                          d['status'] in ('complete', 'failed') and d['processEOF'] is False and
                          d['startedIdentity'] == files[start_name], 'Completed/start binding')
                C.require(all(type(d[k]) is int and d[k] >= 0 for k in
                              ('stdoutStart', 'stdoutEnd', 'stderrStart', 'stderrEnd', 'wireBytes')), 'Wire counters')
                C.require(d['stdoutStart'] == starts[i]['stdoutStart'] and
                          d['stderrStart'] == starts[i]['stderrStart'] and
                          d['wireBytes'] == d['stdoutEnd']-d['stdoutStart']+d['stderrEnd']-d['stderrStart'] and
                          0 <= d['wireBytes'] <= byte_limit, 'Per-case wire delta')
                dones[i] = d
                r.clear()
                r.update(d)
                if d['status'] == 'complete':
                    C.require(d.get('jsonEOF') is True and 'artifact' in d, 'Complete case without full JSON raw')
                else:
                    C.require(d.get('error') or d.get('syncError'), 'Failed case without reason')
                    C.require(d.get('eligiblePositive') is False, 'Failed case claims positive label')
            if raw_name in files:
                C.require(i in dones and dones[i].get('artifact') == raw_name, 'Orphan raw response')
                payload_id, payload = payload_identity(out / raw_name, byte_limit, deadline,
                                                       retain=dones[i]['status'] == 'complete')
                actual = {**files[raw_name], **payload_id}
                C.require(actual == dones[i]['artifactIdentity'], 'Raw SHA/gzip/payload binding')
                artifacts[i] = actual
                if dones[i]['status'] == 'complete':
                    # Pass the journal row itself, not outcome()'s overwritten copy:
                    # complete_contract rechecks every sample and strictly compares
                    # type AND value of all eight independently derived outcome fields.
                    A.complete_contract(C.parse(payload), dones[i], source['game'], source['poolSHA256'])
                    C.require(time.monotonic() < deadline, 'Semantic audit deadline')
                del payload
            if i in dones and 'artifact' in dones[i]:
                C.require(raw_name in files and dones[i]['artifact'] == raw_name, 'Missing raw response')
            if i in starts:
                C.require(i in dones, 'Missing completed journal; started remains interrupted')
        except Exception as e:
            if time.monotonic() >= deadline:
                raise TimeoutError('Full semantic reconciliation exceeded cleanup deadline') from e
            fail(f'Case {i}: {type(e).__name__}: {e}')
            r.update(status='interrupted', error=str(e), eligiblePositive=False)

    seen_actions = set()
    for lane in sorted({r['lane'] for r in rows}):
        directory = out / 'lanes' / lane
        prefix = 'lanes/' + lane + '/'
        try:
            for name in ('started.json', 'completed.json'):
                expected.add(prefix+name)
                C.require(prefix+name in files, 'Missing lane journal: '+name)
            s, d = read_record(directory / 'started.json'), read_record(directory / 'completed.json')
            C.require(s['lane'] == d['lane'] == lane and s['status'] == 'started' and
                      s['startedAt'] == d['startedAt'] and s['finishedAt'] is None and d['finishedAt'] and
                      d['startedIdentity'] == files[prefix+'started.json'], 'Lane journal binding')
            lanes[lane] = d
            declared = {prefix+k: v for k, v in d['files'].items()}
            C.require(set(d['files']) == ({'stdout.gz', 'stderr.log', 'actions.jsonl'} if d['spawned'] else set()),
                      'Lane raw file set')
            expected.update(declared)
            for name, claimed in declared.items():
                C.require(files.get(name) == claimed, 'Lane raw SHA/file missing: ' + name)
            members = sorted((r for r in rows if r['lane'] == lane and r['index'] in starts), key=lambda r: r['index'])
            if not d['spawned']:
                C.require(not members and d['status'] == 'complete' and not d['errors'] and d['wireBytes'] == 0,
                          'Unspawned lane failed or has started cases/wire')
                continue
            # Independent commands catch a missing/rewritten started+completed pair.
            C.require(files[prefix+'actions.jsonl']['bytes'] <= 2*1024**2, 'Command log bound')
            actions = [C.parse(line) for line in (directory / 'actions.jsonl').read_bytes().splitlines()]
            close_count = 0
            action_order = []
            for a in actions:
                if a['command'] == {'cmd': 'close'}:
                    close_count += 1
                    continue
                i = a['index']
                C.require(type(i) is int and 0 <= i < len(rows), 'Command index')
                rows[i]['status'] = 'interrupted' if rows[i]['status'] == 'not-run' else rows[i]['status']
                C.require(i not in seen_actions and i in starts and starts[i]['lane'] == lane and
                          a['seed'] == rows[i]['seed'] and a['pool'] == rows[i]['pool'] and close_count == 0 and
                          a['command'] == dict(cmd='demo', seed=rows[i]['seed'], mode='coop', difficulty='ABYSS', budget=128),
                          'Duplicate/orphan/mismatched command')
                seen_actions.add(i)
                action_order.append(i)
            C.require(action_order == [r['index'] for r in members], 'Started journal/command set or order mismatch')
            C.require(d['status'] == 'complete' and d['closeACKAndProcessEOF'] is True and close_count == 1 and
                      not d['errors'], 'Lane failed/close ACK+process EOF missing')
            C.require(d['wireBytes'] == d['stdoutBytes'] + d['stderrBytes'] and
                      d['stderrBytes'] == files[prefix+'stderr.log']['bytes'], 'Lane wire accounting')
            # Stream the whole gzip to its trailer/EOF and independently bind every
            # case's exact raw payload to its byte interval in the original wire.
            with gzip.open(directory / 'stdout.gz', 'rb') as stream:
                offset = 0
                for r in members:
                    i = r['index']
                    C.require(i in dones and i in artifacts and r['stdoutStart'] == offset, 'Wire gap/missing case')
                    n, h = artifacts[i]['payloadBytes'], hashlib.sha256()
                    while n:
                        C.require(time.monotonic() < deadline, 'Freeze wire deadline')
                        block = stream.read(min(n, 65536))
                        C.require(block, 'Truncated lane wire')
                        h.update(block)
                        n -= len(block)
                        offset += len(block)
                    C.require(h.hexdigest() == artifacts[i]['payloadSHA256'] and offset == r['stdoutEnd'],
                              'Case/wire payload differs')
                tail = stream.read(65537)
                C.require(len(tail) <= 65536 and C.parse(tail) == {'ok': True} and tail.endswith(b'\n') and
                          not stream.read(1), 'Lane close ACK/gzip EOF')
                C.require(offset+len(tail) == d['stdoutBytes'], 'Lane expanded-byte count')
        except Exception as e:
            fail(f'Lane {lane}: {type(e).__name__}: {e}')
            for r in rows:
                if r['lane'] == lane and r['status'] == 'not-run':
                    r.update(status='unknown', error='Lane evidence did not reconcile')

    missing, extra = expected-set(files), set(files)-expected
    if missing or extra:
        fail('File set mismatch: missing=' + repr(sorted(missing)) + ' extra=' + repr(sorted(extra)))
    if any(r['status'] in ('failed', 'interrupted') for r in rows):
        fail('Failed/interrupted cases exist')
    if ambiguous:
        fail('Unattributable evidence; untouched execution status is unknown')
        for r in rows:
            if r['status'] == 'not-run':
                r.update(status='unknown', error='Unattributable evidence')
    C.require(sum(v['bytes'] for v in files.values()) <= DISK_BYTES, 'Disk inventory exceeds budget')
    C.require(sum(d['wireBytes'] for d in lanes.values()) <= len(rows)*byte_limit, 'Aggregate wire ceiling')
    return dict(games=rows, lanes=lanes, files=files, filesSHA256=C.digest(C.encoded(files)), errors=errors)
