"""Frozen three-model dev evaluation using the original E.game_job unchanged."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import time

from contracts_fit import (C, E, G, GV, ARMS, MODELS, CONFIG, MANIFEST_SHA, PARENT_SHA,
    MIB, LIMITATIONS, identity, read_json, safe_path, model_guard, recheck_inputs)
from training import epoch_orders, critic_sha


def schedule():
    return [{**r, 'id': r['id'].replace('parent-', name + '-', 1), 'model': name,
             'episodeGroup': r['seed']}
            for name in MODELS for r in E.schedule() if r['model'] == 'parent']


def summary(rows):
    C.require(len(rows) == 96 and [r['id'] for r in rows] == [r['id'] for r in schedule()], 'Fixed 96-game plan')
    def count(rs):
        team = sum(r['status'] == 'complete' and r['teamVictory'] is True for r in rs)
        individual = sum(r['status'] == 'complete' and r['learnerIndividualVictory'] is True for r in rs)
        return dict(denominator=len(rs), complete=sum(r['status'] == 'complete' for r in rs),
            technicalFailures=sum(r['status'] == 'failed' for r in rs),
            unrun=sum(r['status'] == 'unrun' for r in rs), teamWins=team, learnerWins=individual,
            teamWinsPerScheduled=team / len(rs), learnerWinsPerScheduled=individual / len(rs))
    return dict(**count(rows), independentSeedGroups=16,
        cells={f'{m}/{p}': count([r for r in rows if r['model'] == m and r['pool'] == p])
               for m in MODELS for p in C.TEAMS})


def invalidate(rows, error):
    for row in rows:
        if row['status'] == 'complete':
            row.update(status='failed', error='Run invalidated: ' + str(error)[:2000],
                       teamVictory=None, learnerIndividualVictory=None)


def run_game(row, model, ckpt, code, game_path, out, deadline, byte_limit, pool):
    safe_path(out / row['id'])
    # Original protocol, state_contract, teacher=False terminal contract, raw/EOF handling.
    result = E.game_job(row, model, ckpt, code, game_path, out, deadline, byte_limit - 16384, pool)
    path = safe_path(out / row['id'] / 'result.json')
    C.require(read_json(path, 16384) == result, 'Result metadata at publication')
    result['resultFile'] = identity(path, 16384)
    return result


def verify_results(rows, out, control):
    safe_path(out)
    directories = {r['id'] for r in rows if (out / r['id']).exists() or (out / r['id']).is_symlink()}
    C.require({p.name for p in out.iterdir()} == set(control) | directories | {'summary.json'}, 'Eval output file set drift')
    for name, expected in control.items():
        C.require(identity(out / name) == expected, 'Eval control evidence drift')
    safe_path(out / 'summary.json')
    for row in rows:
        directory = safe_path(out / row['id'])
        C.require(row['status'] != 'unrun' or not directory.exists(), 'Unrun game has unexpected output')
        if directory.exists():
            C.require(directory.is_dir(), 'Game evidence must be a directory')
            for child in directory.iterdir():
                safe_path(child)
                C.require(child.is_file(), 'Unexpected evaluation subdirectory')
        if row['status'] == 'complete':
            C.require(row.get('eof') is True and row.get('resultFile'), 'Complete requires EOF/result identity')
    GV.verify_evaluation_evidence(rows, out)


def dispatch(rows, models, code, game_path, out, deadline, workers, timeout, byte_limit, pool, job=run_game):
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending, index = {}, 0
        while pending or index < len(rows):
            while len(pending) < workers and index < len(rows) and time.monotonic() < deadline:
                r = rows[index]
                f = executor.submit(job, r, *models[r['model']], code, game_path, out,
                                    min(deadline, time.monotonic() + timeout), byte_limit, pool)
                pending[f] = index
                index += 1
            if not pending:
                break
            done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
            for f in done:
                i = pending.pop(f)
                try:
                    rows[i] = f.result()
                except Exception as error:
                    rows[i] = {**rows[i], 'status': 'failed', 'error': str(error)[:2000]}
            safe_path(out / 'summary.json')
            C.write(out / 'summary.json', dict(status='running', creditGranted=False, **summary(rows), games=rows))


def evaluate(L, trained, parent, code, game_path, game, pin, out, source, rows, deadline, guard,
             workers=4, timeout=120, byte_limit=64 * MIB):
    C.require(1 <= workers <= 4 and 0 < timeout <= 120 and 16384 < byte_limit <= 64 * MIB, 'Eval bounds')
    safe_path(trained)
    report = read_json(trained / 'train.json')
    report_id = identity(trained / 'train.json')
    C.require(report['status'] == 'complete' and report['kind'] == 'bc-fit-budget/v1' and
        report['source'] == source and report['game'] == game and report['gameIdentity'] == pin and
        report['config'] == CONFIG and report['configSHA256'] == C.digest(C.encoded(CONFIG)) and
        report['manifestSHA256'] == MANIFEST_SHA and set(report['models']) == set(ARMS) and
        report['parent']['sha256'] == PARENT_SHA and report['parent']['path'] == str(parent) and
        report['positiveLabels'] == 498 and report['positiveGames'] == 6, 'Frozen complete training required')

    def check_evidence():
        safe_path(trained)
        C.require({p.name for p in trained.iterdir()} == set(report['evidence']) | {'train.json'}, 'Train file set drift')
        C.require(identity(trained / 'train.json') == report_id, 'Train report drift')
        for name, expected in report['evidence'].items():
            C.require('/' not in name and name not in ('.', '..'), 'Evidence basename')
            C.require(identity(trained / name) == expected, 'Train evidence drift: ' + name)

    check_evidence()
    metadata = read_json(trained / 'input-metadata.json')
    paths = read_json(trained / 'admission-paths.json')
    C.require(identity(trained / 'recapture-manifest.json')['sha256'] == MANIFEST_SHA and
        identity(trained / 'input-metadata.json')['sha256'] == report['metadataSHA256'] and
        identity(trained / 'draw-plan.json')['sha256'] == report['drawPlanSHA256'] and
        metadata['inputSHA256'] == report['inputSHA256'] and
        metadata['inputReviewStatus'] == report['inputReviewStatus'] and
        metadata['episodeGroups'] == report['episodeGroups'], 'Training input provenance')
    recheck_inputs(metadata, paths)
    plan = read_json(trained / 'draw-plan.json')
    C.require(plan == dict(selectedIndices=list(range(498)), expandedIndices=[i % 498 for i in range(6800)],
        epochOrders=epoch_orders(L, 15), orderSeed=101, epochs=ARMS), 'Frozen draw plan')
    initial, parent_ckpt = L.load_checkpoint(parent)
    model_guard(L, initial, parent_ckpt, game)
    C.require(L.digest(initial) == report['parent']['parameterSHA256'], 'Parent parameters')
    models = {'parent': (initial, parent_ckpt)}
    for name, epochs in ARMS.items():
        record = report['models'][name]
        C.require(identity(trained / (name + '.pt'))['sha256'] == record['checkpointSHA256'], 'Model SHA')
        model, ckpt = L.load_checkpoint(trained / (name + '.pt'))
        model_guard(L, model, ckpt, game)
        C.require(L.digest(model) == record['parameterSHA256'] and
            record['initialParameterSHA256'] == L.digest(initial) and record['criticHeadUnchanged'] is True and
            record['actualUpdates'] == epochs * 200 and record['actualDraws'] == epochs * 6800 and
            record['epochs'] == epochs and record['training']['samples'] == 6800, 'Actual arm budget / parameters')
        md = ckpt['metadata']
        provenance = ('config', 'configSHA256', 'source', 'game', 'gameIdentity', 'parent', 'manifestSHA256',
            'inputSHA256', 'schema', 'inputReviewStatus', 'limitations', 'metadataSHA256', 'drawPlanSHA256',
            'positiveLabels', 'positiveGames', 'episodeGroups')
        expected_md = dict(**{k: report[k] for k in provenance}, algorithm='BC', arm=name,
            features='OFF global[:5267]; unchanged candidates/mask',
            metrics={k: v for k, v in record.items() if k != 'checkpointSHA256'})
        C.require(md == expected_md, 'Checkpoint provenance')
        actual = read_json(trained / (name + '-accesses.json'))
        order = [i for epoch in plan['epochOrders'][:epochs] for i in epoch]
        indices = [i % 498 for i in order]
        counts = Counter(indices)
        groups = Counter(metadata['labels'][i]['episodeGroup'] for i in indices)
        C.require(actual == dict(accesses=order, labelIndices=indices) and
            identity(trained / (name + '-accesses.json'))['sha256'] == record['accessesSHA256'] and
            C.digest(C.encoded(order)) == record['actualOrderSHA256'] and record['actualDistinctDrawnLabels'] == 498 and
            record['actualLabelDrawCounts'] == [counts[i] for i in range(498)] and
            record['actualGroupDrawCounts'] == [dict(episodeGroup=g, draws=groups[g]) for g in metadata['episodeGroups']],
            'Actual access evidence')
        adam = record['adamSteps']
        C.require(set(adam) == {n for n, _ in model.named_parameters() if not n.startswith('critic.')} and
            all(v == epochs * 200 for v in adam.values()) and
            len(ckpt['optimizer']['state']) == len(adam) and
            all(int(v['step']) == epochs * 200 for v in ckpt['optimizer']['state'].values()), 'Saved Adam steps')
        C.require(all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items()
                      if k.startswith('critic.')), 'Critic head changed')
        C.require(record['initialCriticHeadSHA256'] == record['finalCriticHeadSHA256'] == critic_sha(L, initial),
                  'Critic head SHA')
        models[name] = (model, ckpt)
    hashes = {}
    for name, (model, _) in models.items():
        model.eval()
        model.requires_grad_(False)
        hashes[name] = L.digest(model)
    C.write(safe_path(out / 'admission.json'), dict(trained=str(trained), trainIdentity=report_id,
        models=hashes, source=source, game=game, gameIdentity=pin, parent=report['parent'],
        inputSHA256=report['inputSHA256'], manifestSHA256=MANIFEST_SHA, limitations=LIMITATIONS))
    control = {n: identity(out / n) for n in ('manifest.json', 'admission.json')}
    dispatch(rows, models, code, game_path, out, deadline, workers, timeout, byte_limit, G.pool_sha(code))
    final_guard = guard()
    check_evidence()
    recheck_inputs(metadata, paths)
    for name, (model, _) in models.items():
        C.require(L.digest(model) == hashes[name], 'Frozen model drift')
    verify_results(rows, out, control)
    return dict(**final_guard, inputEvidenceVerified=True, trainingEvidenceVerified=True,
                frozenParameterSHA256=hashes, payloadAndResultsVerified=True)
