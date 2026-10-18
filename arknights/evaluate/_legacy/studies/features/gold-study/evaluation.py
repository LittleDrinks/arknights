"""96 paired dev games using the unmodified survivor Protocol and contracts."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import time

from contracts import (C, E, CODE, MIB, MODES, CONFIG, REVIEW, LIMITATIONS,
                       bounded, read_json, model_guard, project, environment, pool_sha, recheck, load)


def schedule():
    return [{**r, 'id': r['id'].replace('parent-', name + '-', 1), 'model': name,
             'goldMode': mode, 'episodeGroup': r['seed']}
            for name, mode in MODES.items() for r in E.schedule() if r['model'] == 'parent']


def summary(rows):
    C.require(len(rows) == 96 and [r['id'] for r in rows] == [r['id'] for r in schedule()], 'Fixed 96-game plan')
    def count(rs):
        team = sum(r['status'] == 'complete' and r['teamVictory'] is True for r in rs)
        learner = sum(r['status'] == 'complete' and r['learnerIndividualVictory'] is True for r in rs)
        return dict(denominator=len(rs), complete=sum(r['status'] == 'complete' for r in rs),
                    failed=sum(r['status'] == 'failed' for r in rs), unrun=sum(r['status'] == 'unrun' for r in rs),
                    teamWins=team, learnerWins=learner, teamWinsPerScheduled=team / len(rs),
                    learnerWinsPerScheduled=learner / len(rs))
    return dict(**count(rows), independentSeedGroups=16,
                cells={f'{m}/{p}': count([r for r in rows if r['model'] == m and r['pool'] == p])
                       for m in MODES for p in C.TEAMS})


def verify_evaluation_evidence(rows, out):
    """Reconcile the evidence of finished games before granting final success credit."""
    required = {'protocol.jsonl', 'actions.jsonl', 'stderr.log', 'terminal.json'}
    for row in rows:
        if row['status'] == 'unrun':
            continue
        if 'files' not in row:
            C.require(row['status'] == 'failed', 'Completed game lacks evaluation evidence')
            continue
        directory = out / row['id']
        files = row['files']
        C.require(row['status'] != 'complete' or required <= set(files), 'Incomplete evaluation evidence')
        C.require({p.name for p in directory.iterdir()} == set(files) | {'result.json'},
                  'Evaluation evidence file set drift')
        for name, identity in files.items():
            C.require('/' not in name and name not in ('.', '..', 'result.json'), 'Evaluation evidence name')
            path = directory / name
            C.require(path.is_file() and not path.is_symlink() and path.stat().st_size == identity['bytes'] and
                      C.sha(path) == identity['sha256'], 'Evaluation evidence drift: ' + row['id'] + '/' + name)
        saved = directory / 'result.json'
        identity = row['resultFile']
        C.require(saved.is_file() and not saved.is_symlink() and saved.stat().st_size == identity['bytes'] and
                  C.sha(saved) == identity['sha256'], 'Evaluation result file drift')
        C.require(read_json(saved, 16384) == {k: v for k, v in row.items() if k != 'resultFile'},
                  'Evaluation result metadata drift')


def game_job(row, model, ckpt, code, game, out, deadline, byte_limit, pool_manifest):
    result, steps, started, channel = dict(row), 0, time.monotonic(), None
    directory = out / row['id']
    directory.mkdir(exist_ok=False)
    try:
        C.require(byte_limit > 16384, 'Per-game budget must reserve result metadata')
        mode = row['goldMode']
        C.require(MODES.get(row['model']) == mode, 'Model/mode mismatch')
        env = environment(code, mode, row['pool'])
        channel = E.Protocol(['node', '--no-warnings', '--loader', str(code / 'gold-features/loader.mjs'),
                              str(code / 'coop/env.mjs'), '--game', str(game)], env, directory, deadline, byte_limit - 16384)
        command = dict(cmd='reset', seed=row['seed'], mode='coop', difficulty='ABYSS', budget=128, teacher=False)
        while True:
            state, raw = channel.request(command)
            if state.get('done') is True:
                channel.charge(len(raw))
                with (directory / 'terminal.json').open('xb') as stream:
                    stream.write(raw)
            projected = project(state, ckpt['schema'], mode)
            old_ckpt = {**ckpt, 'schema': projected['schema']}
            E.state_contract(projected, row, old_ckpt, steps, pool_manifest)
            if state['done']:
                individual = C.terminal_contract(projected, seed=row['seed'], steps=steps, schema=projected['schema'])
                channel.finish()  # Original strict close acknowledgement, drained stderr and EOF.
                result.update(status='complete', teamVictory=state['summary']['victory'],
                    learnerIndividualVictory=individual['victory'],
                    learnerOutcome={k: individual[k] for k in ('playerId', 'seat', 'victory', 'alive',
                                                               'roundsPassed', 'eliminatedRound', 'lp')},
                    rounds=state['rounds'], eof=True, engineErrors=state['engineErrors'],
                    actionErrors=state['actionErrors'], controllerErrors=state['controllerErrors'],
                    controllerDiagnosticErrors=state['controllerDiagnosticErrors'],
                    partnerActionErrors=state['partnerActionErrors'])
                break
            C.require(steps < 4096, 'Maximum policy steps exceeded')
            action, _, _ = model.act(state['features'], greedy=True)
            C.require(type(action) is int and 0 <= action < len(state['candidates']) and
                      state['features']['mask'][action], 'Illegal greedy action')
            channel.action(dict(step=steps, round=state['rounds'], seat=0, candidates=state['candidates'],
                                mask=state['features']['mask'], candidateIndex=action, action=state['candidates'][action]))
            command = dict(cmd='step', candidateIndex=action)
            steps += 1
    except Exception as error:
        result.update(status='failed', error=f'{type(error).__name__}: {error}'[:4000], eof=False)
    finally:
        if channel:
            channel.close()
        result.update(steps=steps, seconds=time.monotonic() - started, outputBytes=channel.bytes if channel else 0)
        result['files'] = {p.name: dict(sha256=C.sha(p), bytes=p.stat().st_size) for p in sorted(directory.iterdir()) if p.is_file()}
        C.require(len(C.encoded(result)) <= 16384, 'Per-game result metadata reserve')
        C.write(directory / 'result.json', result)
        result['resultFile'] = dict(sha256=C.sha(directory / 'result.json'), bytes=(directory / 'result.json').stat().st_size)
        C.require(len(C.encoded(result)) <= 16384, 'Per-game result metadata reserve')
    return result


def evaluate(L, trained, parent, code, game_path, game, out, source, rows, deadline, guard,
             workers=4, timeout=120, byte_limit=64 * MIB):
    C.require(1 <= workers <= 4 and 0 < timeout <= 120 and 0 < byte_limit <= 64 * MIB, 'Evaluation bounds')
    report = read_json(trained / 'train.json')
    report_hash = C.sha(trained / 'train.json')
    C.require(report['status'] == 'complete' and report['kind'] == 'gold-study/v1' and report['source'] == source and
              report['game'] == game and report['config'] == CONFIG and
              report['configSHA256'] == C.digest(C.encoded(CONFIG)) and report['inputReviewStatus'] == REVIEW and
              set(report['models']) == set(MODES), 'Complete frozen training report required')
    C.require(C.sha(parent) == report['parent']['sha256'] and str(parent) == report['parent']['path'], 'Same explicit parent')
    C.require({p.name for p in trained.iterdir()} == set(report['evidence']) | {'train.json'}, 'Training evidence file set')

    def check_evidence():
        C.require({p.name for p in trained.iterdir()} == set(report['evidence']) | {'train.json'}, 'Training evidence file set drift')
        C.require(C.sha(trained / 'train.json') == report_hash, 'Training report drift')
        for name, identity in report['evidence'].items():
            C.require('/' not in name and name not in ('.', '..'), 'Evidence name')
            raw = bounded(trained / name, 64 * MIB)
            C.require(len(raw) == identity['bytes'] and C.digest(raw) == identity['sha256'], 'Training evidence drift: ' + name)

    check_evidence()
    metadata = read_json(trained / 'input-metadata.json')
    C.require(C.sha(trained / 'input-metadata.json') == report['metadataSHA256'] and
              C.sha(trained / 'draw-plan.json') == report['drawPlanSHA256'] and
              metadata['inputSHA256'] == report['inputSHA256'], 'Training provenance hashes')
    recheck(metadata)
    _, original = L.load_checkpoint(parent)
    warm, warm_ckpt = load('gold_warm_eval', CODE / 'gold-features/warm_start.py').warm_start(original, L.ActorCritic)
    C.require(report['schema'] == warm_ckpt['schema'] and report['initialParameterSHA256'] == L.digest(warm), 'Warm baseline identity')
    models = {}
    provenance_keys = ('source', 'game', 'parent', 'config', 'configSHA256', 'inputSHA256', 'inputReviewStatus',
                       'limitations', 'metadataSHA256', 'drawPlanSHA256', 'schema', 'positiveLabels', 'episodeGroups',
                       'initialParameterSHA256')
    for name, mode in MODES.items():
        record = report['models'][name]
        C.require(record['checkpointSHA256'] == C.sha(trained / (name + '.pt')), 'Checkpoint SHA')
        model, ckpt = L.load_checkpoint(trained / (name + '.pt'))
        model_guard(L, model, ckpt, report['schema'])
        C.require(L.digest(model) == record['parameterSHA256'] and record['mode'] == mode and
                  record['initialParameterSHA256'] == L.digest(warm) and record['criticHeadUnchanged'] is True, 'Model parameters/mode')
        md = ckpt['metadata']
        C.require(all(md[k] == report[k] for k in provenance_keys) and md['mode'] == mode and md['arm'] == name and
                  md['metrics'] == {k: v for k, v in record.items() if k != 'checkpointSHA256'}, 'Checkpoint metadata')
        expected_updates, expected_draws = (0, 0) if name == 'warmBaseline' else (200, 6800)
        C.require(record['actualUpdates'] == expected_updates and record['actualDraws'] == expected_draws and
                  sum(record['actualLabelDrawCounts']) == expected_draws and
                  sum(r['draws'] for r in record['actualGroupDrawCounts']) == expected_draws, 'Actual training budget')
        C.require(all(L.torch.equal(v, warm.state_dict()[k]) for k, v in model.state_dict().items()
                      if name == 'warmBaseline' or k.startswith('critic.')), 'Warm parameters/critic head')
        model.eval()
        model.requires_grad_(False)
        models[name] = (model, ckpt)
    C.write(out / 'admission.json', dict(trained=str(trained), trainSHA256=report_hash, source=source,
        game=game, models=report['models'], inputSHA256=report['inputSHA256'], inputReviewStatus=REVIEW,
        limitations=LIMITATIONS))
    manifest_pool = pool_sha(code)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending, next_index = {}, 0
        while pending or next_index < len(rows):
            while len(pending) < workers and next_index < len(rows) and time.monotonic() < deadline:
                i = next_index
                r = rows[i]
                f = executor.submit(game_job, r, *models[r['model']], code, game_path, out,
                                    min(deadline, time.monotonic() + timeout), byte_limit, manifest_pool)
                pending[f] = i
                next_index += 1
            if not pending:
                break
            done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
            for f in done:
                i = pending.pop(f)
                try:
                    rows[i] = f.result()
                except Exception as error:
                    rows[i] = {**rows[i], 'status': 'failed', 'error': str(error)[:2000]}
            C.write(out / 'summary.json', dict(status='running', **summary(rows), games=rows))
    guard()
    verify_evaluation_evidence(rows, out)
    check_evidence()
    recheck(metadata)
    for name, (model, _) in models.items():
        C.require(L.digest(model) == report['models'][name]['parameterSHA256'], 'Model mutated during evaluation')
