"""64 fixed dev games. Only the unchanged reviewed game_job executes a game."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import time

from support import C, E, MIB, REVIEW, LIMITATIONS, bounded, read_json, model_guard, pool_sha


def schedule():
    return [r for r in E.schedule() if r['model'] == 'parent'] + [
        {**r, 'model': 'naturalWins', 'id': r['id'].replace('parent-', 'naturalWins-', 1)}
        for r in E.schedule() if r['model'] == 'parent']


def summary(rows):
    C.require(len(rows) == 64 and [r['id'] for r in rows] == [r['id'] for r in schedule()], 'Fixed 64-game denominator')
    def counts(rs):
        return dict(denominator=len(rs), complete=sum(r['status'] == 'complete' for r in rs),
                    failed=sum(r['status'] == 'failed' for r in rs), unrun=sum(r['status'] == 'unrun' for r in rs),
                    teamWins=sum(r['status'] == 'complete' and r['teamVictory'] is True for r in rs),
                    learnerWins=sum(r['status'] == 'complete' and r['learnerIndividualVictory'] is True for r in rs))
    result = counts(rows)
    result['cells'] = {f'{m}/{p}': counts([r for r in rows if r['model'] == m and r['pool'] == p])
                       for m in ('parent', 'naturalWins') for p in ('original', 'mixed')}
    return result


def evaluate(L, trained, parent, code, game_path, game, out, source, rows, deadline, guard,
             workers=4, timeout=120, byte_limit=64 * MIB):
    report = read_json(trained / 'train.json')
    report_hash = C.sha(trained / 'train.json')
    C.require(report['status'] == 'complete' and report['kind'] == 'natural-win-bc/v1' and
              report['config'] == C.CONFIG and report['configSHA256'] == C.digest(C.encoded(C.CONFIG)) and
              report['source'] == source and report['game'] == game and report['inputReviewStatus'] == REVIEW,
              'Complete train/source/config required')
    identities = {n: report[k] for n, k in [('input-metadata.json.gz', 'metadataSHA256'), ('selection.json', 'selectionSHA256')]}
    for name, expected in identities.items():
        C.require(C.digest(bounded(trained / name, 64 * MIB)) == expected, 'Training evidence changed')
    C.require(report['result']['actualDraws'] == 6800 and report['result']['actualUpdates'] == 200 and
              report['result']['criticHeadUnchanged'] is True and
              report['result']['initialParameterSHA256'] == report['parent']['parameterSHA256'], 'Training budget/initial model')
    models, hashes = {}, {}
    for name, path in [('parent', parent), ('naturalWins', trained / 'naturalWins.pt')]:
        expected = report['parent']['sha256'] if name == 'parent' else report['result']['checkpointSHA256']
        C.require(C.digest(bounded(path, 64 * MIB)) == expected, 'Model SHA mismatch')
        hashes[name] = expected
        model, ckpt = L.load_checkpoint(path)
        model_guard(L, model, ckpt, game)
        params = report['parent']['parameterSHA256'] if name == 'parent' else report['result']['parameterSHA256']
        C.require(L.digest(model) == params, 'Parameter SHA mismatch')
        if name == 'naturalWins':
            md = ckpt['metadata']
            for key in ('source', 'game', 'parent', 'config', 'configSHA256', 'inputSHA256', 'manifestSHA256',
                        'metadataSHA256', 'selectionSHA256', 'inputReviewStatus', 'trainingEpisodeGroups',
                        'trainingGroupCount', 'eligibleEpisodeGroups', 'eligibleGroupCount', 'distinctDrawnLabels',
                        'positiveLabels', 'positiveGames', 'selector', 'arm', 'algorithm'):
                C.require(md[key] == report[key], 'Checkpoint provenance: ' + key)
            C.require(md['metrics'] == {k: v for k, v in report['result'].items() if k not in ('checkpoint', 'checkpointSHA256')},
                      'Checkpoint metric provenance')
        model.eval()
        model.requires_grad_(False)
        models[name] = (model, ckpt)
    C.require(all(L.torch.equal(v, models['parent'][0].state_dict()[k])
                  for k, v in models['naturalWins'][0].state_dict().items() if k.startswith('critic.')), 'Critic changed')
    C.write(out / 'admission.json', dict(trained=str(trained), parent=str(parent), trainSHA256=report_hash,
                                        source=source, game=game, models=hashes,
                                        inputReviewStatus=REVIEW, limitations=LIMITATIONS))
    pool_manifest = pool_sha(code)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending, next_index = {}, 0
        while pending or next_index < len(rows):
            while len(pending) < workers and next_index < len(rows) and time.monotonic() < deadline:
                i = next_index
                r = rows[i]
                f = executor.submit(E.game_job, r, *models[r['model']], code, game_path, out,
                                    min(deadline, time.monotonic() + timeout), byte_limit, pool_manifest)
                pending[f] = i
                next_index += 1
            if not pending:
                break
            done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
            for f in done:
                i = pending.pop(f)
                try:
                    rows[i] = f.result()
                except Exception as exc:
                    rows[i] = {**rows[i], 'status': 'failed', 'error': str(exc)[:2000]}
            C.write(out / 'summary.json', dict(status='running', **summary(rows), games=rows))
    guard()
    C.require(C.sha(trained / 'train.json') == report_hash, 'Train report changed')
    for name, expected in identities.items():
        C.require(C.sha(trained / name) == expected, 'Training evidence drift')
    for name, (model, _) in models.items():
        path = parent if name == 'parent' else trained / 'naturalWins.pt'
        params = report['parent']['parameterSHA256'] if name == 'parent' else report['result']['parameterSHA256']
        C.require(C.sha(path) == hashes[name] and L.digest(model) == params, 'Model changed during eval')
