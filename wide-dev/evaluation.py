"""Paired development schedule, original P jobs, reservation-based bounded dispatch."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait, FIRST_COMPLETED
import multiprocessing
import signal
import time

from contracts_wide import C, F, P, MODELS, MIB, TOTAL_BYTES, META_BYTES, digest, require, safe, write_new


def schedule():
    # Pair neighbours and complete each seed's eight cells before the next seed.
    return [dict(id=f'{model}-{controller}-{pool}-{seed:03}', model=model, controller=controller,
                 pool=pool, seed=seed, episodeGroup=seed, mode='coop', difficulty='ABYSS', seat=0,
                 status='unrun', steps=0, eof=False, error=None, teamVictory=None, learnerIndividualVictory=None)
            for seed in range(1, 257) for controller in P.VERSIONS for pool in C.TEAMS for model in MODELS]


def summary(rows):
    require([r['id'] for r in rows] == [r['id'] for r in schedule()], 'Fixed ordered 2048-row plan')
    def count(items):
        result = dict(denominator=len(items), **{s: sum(r['status'] == s for r in items)
                      for s in ('complete', 'failed', 'stopped', 'unrun')})
        for label, key in (('team', 'teamVictory'), ('individual', 'learnerIndividualVictory')):
            result[label + 'Wins'] = sum(r['status'] == 'complete' and r[key] is True for r in items)
            result[label + 'WinsPerScheduled'] = result[label + 'Wins'] / len(items)
        return result
    cells, paired = {}, {}
    index = {(r['model'], r['controller'], r['pool'], r['seed']): r for r in rows}
    for controller in P.VERSIONS:
        for pool in C.TEAMS:
            for model in MODELS:
                cells[f'{model}/{controller}/{pool}'] = count([index[model, controller, pool, s] for s in range(1, 257)])
            pair = dict(denominator=256, bothComplete=0, incompletePairs=0)
            for label in ('team', 'individual'):
                pair[label] = dict(baselineFailCandidateWin=0, baselineWinCandidateFail=0, bothWin=0, bothFail=0,
                                   baselineTechnicalFailureCandidateWin=0)
            for seed in range(1, 257):
                parent, candidate = (index[m, controller, pool, seed] for m in MODELS)
                complete = parent['status'] == candidate['status'] == 'complete'
                pair['bothComplete' if complete else 'incompletePairs'] += 1
                for label, key in (('team', 'teamVictory'), ('individual', 'learnerIndividualVictory')):
                    a, b = parent[key], candidate[key]
                    if complete:
                        which = ('bothWin' if a else 'baselineFailCandidateWin') if b else (
                                 'baselineWinCandidateFail' if a else 'bothFail')
                        pair[label][which] += 1
                    elif parent['status'] in ('failed', 'stopped') and candidate['status'] == 'complete' and b is True:
                        pair[label]['baselineTechnicalFailureCandidateWin'] += 1
            paired[f'{controller}/{pool}'] = pair
    return dict(**count(rows), pairedSeedGroups=256, cells=cells, paired=paired)


def invalidate(rows, error):
    for row in rows:
        if row['status'] == 'complete':
            row.update(status='failed', teamVictory=None, learnerIndividualVictory=None,
                       creditRevoked=True, error='Global integrity failure: ' + str(error)[:2000])


def episode_seal(directory, deadline):
    directory = safe(directory)
    info = directory.stat()
    require(directory.is_dir(), 'Episode directory missing')
    files, inodes = {}, {}
    for path in sorted(directory.iterdir()):
        files[path.name] = digest(path, deadline)
        item = path.stat()
        inodes[path.name] = [item.st_dev, item.st_ino]
    return dict(directory=[info.st_dev, info.st_ino], files=files, inodes=inodes,
                bytes=sum(v['bytes'] for v in files.values()))


def verify_job(row, out, seal, byte_limit, deadline):
    directory = safe(out / row['id'])
    require(episode_seal(directory, deadline) == {k: v for k, v in seal.items() if k != 'workerProof'} and
            seal['bytes'] <= byte_limit, 'Episode evidence drift/budget')
    result = F.read_json(directory / 'result.json', P.RESULT_RESERVE)
    extras = {'result.json'}
    if 'workerProof' in seal:
        require(F.read_json(directory / 'inference-proof.json', 32768) == seal['workerProof'], 'Worker proof evidence drift')
        extras.add('inference-proof.json')
    require(result == row and row['files'] == {k: v for k, v in seal['files'].items() if k not in extras},
            'Published result/body/payload mismatch')
    require(row['status'] in ('complete', 'failed', 'stopped'), 'Unknown job status')
    if row['status'] == 'complete':
        require(row['eof'] is True and {'protocol.jsonl', 'stderr.log', 'actions.jsonl', 'terminal.json'} | extras ==
                set(seal['files']), 'Complete requires original raw, terminal, close/EOF and result')


def accept_worker(row, result, seal, config, out, byte_limit):
    from admission_wide import proof_identity
    require(all(result[k] == row[k] for k in ('id', 'model', 'controller', 'pool', 'seed', 'episodeGroup',
                                            'mode', 'difficulty', 'seat')), 'Job identity changed')
    proof = seal['workerProof']
    require(type(proof['pid']) is int and proof['pid'] > 0, 'Worker PID proof')
    require(proof == dict(kind='wide-dev/inference-proof-v1', job=row['id'], model=row['model'], pid=proof['pid'],
            before=proof_identity(config['models'][row['model']]), after=proof_identity(config['models'][row['model']]), frozen=True,
            intraThreads=1, interThreads=1, affinity=config['affinity'], checkpoint=config['files'][row['model']],
            sourceSHA256=C.digest(C.encoded(config['source']))), 'Actual worker inference proof mismatch')
    original = {k: v for k, v in seal.items() if k != 'workerProof'}
    verify_job(result, out, original, byte_limit - 32768, config['deadline'])
    write_new(out / row['id'] / 'inference-proof.json', proof, 32768)
    final = episode_seal(out / row['id'], config['deadline'])
    final['workerProof'] = proof
    verify_job(result, out, final, byte_limit, config['deadline'])
    return result, final


def dispatch(rows, models, game, out, deadline, workers, timeout, byte_limit, pool, *, job=None,
             worker_config=None, total_bytes=TOTAL_BYTES, metadata_bytes=META_BYTES):
    require(1 <= workers <= 16 and 0 < timeout <= 120 and P.RESULT_RESERVE < byte_limit <= 64 * MIB,
            'Finite dispatch limits')
    seals, errors, pending = {}, [], {}
    index, used, reserved = 0, metadata_bytes, 0
    hard, protected = False, set()
    if worker_config is not None:
        import processes
        from workers import initialize, perform_job
        require(job is None, 'Production pool uses only the original inference worker')
        job = perform_job
        processes.adopt_orphans()
        protected = processes.infrastructure_pids()
        executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'),
                                       initializer=initialize, initargs=(worker_config,))
    else:
        require(job is not None, 'Synthetic scheduler requires an explicit fake job')
        executor = ThreadPoolExecutor(max_workers=workers)
    try:
        while pending or index < len(rows):
            try:
                if worker_config is not None and time.monotonic() >= worker_config['deadline']:
                    hard = True
                    errors.append('Absolute pool deadline; terminate/reap remaining owned processes')
                    for i in pending.values():
                        rows[i].update(status='failed', evidenceUnsealed=True, error=errors[-1])
                    break
                while (not errors and index < len(rows) and len(pending) < workers and
                       time.monotonic() < deadline and used + reserved + byte_limit <= total_bytes):
                    row = rows[index]
                    job_deadline = min(deadline, time.monotonic() + timeout)
                    # Do not lose an already-started future if the CLI alarm
                    # lands between submit() and registration in pending.
                    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
                    try:
                        if worker_config is None:
                            future = executor.submit(job, dict(row), *models[row['model']], game, out,
                                                     job_deadline, byte_limit, pool)
                        else:
                            future = executor.submit(job, dict(row), job_deadline, byte_limit, pool)
                        pending[future] = index
                        reserved += byte_limit
                        index += 1
                    finally:
                        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                if not pending:
                    break
                done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
                # Consume ALL ready futures before another dispatch; an exception
                # must not be hidden by a successful future from the same wait.
                for future in done:
                    i = pending[future]
                    try:
                        result, seal = future.result()
                        if worker_config is not None:
                            result, seal = accept_worker(rows[i], result, seal, worker_config, out, byte_limit)
                        require(seal['bytes'] <= byte_limit, 'Job reservation exceeded')
                        rows[i], seals[rows[i]['id']] = result, seal
                        used += seal['bytes']
                    except BaseException as exc:
                        message = f'{type(exc).__name__}: {exc}'[:2000]
                        rows[i].update(status='failed', error=message, evidenceUnsealed=True)
                        errors.append(rows[i]['id'] + ': ' + message)
                    finally:
                        del pending[future]
                        reserved -= byte_limit
            except BaseException as exc:
                # Includes CLI signal/submit/wait exceptions. Keep pending and drain.
                errors.append(f'{type(exc).__name__}: {exc}'[:2000])
                if not pending:
                    break
    finally:
        if worker_config is None:
            executor.shutdown(wait=True)
        else:
            orphans = processes.shutdown(executor, hard, protected)
            if orphans:
                errors.append('Orphan child processes required cleanup: ' + str(orphans))
    if errors:
        invalidate(rows, '; '.join(errors))
    return seals, errors


def audit(out, rows, seals, controls, byte_limit, deadline):
    require({p.name for p in safe(out).iterdir()} == set(controls) | set(seals), 'Unexpected/missing output entries')
    for name, expected in controls.items():
        require(digest(out / name, deadline) == expected, 'Control evidence drift')
    for row in rows:
        if row['id'] in seals:
            verify_job(row, out, seals[row['id']], byte_limit, deadline)
        else:
            require(row['status'] == 'unrun', 'Missing trusted job seal')
    require(sum(s['bytes'] for s in seals.values()) + META_BYTES <= TOTAL_BYTES, '64GiB aggregate evidence bound')
