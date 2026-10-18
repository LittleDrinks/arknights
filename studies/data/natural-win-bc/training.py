"""One positive-only arm, original learner, exactly the reviewed study budget."""
from collections import Counter
from support import C, T, LIMITATIONS, REVIEW, SELECTOR, MIB, model_guard


def train(L, parent, samples, metadata, out, source, game, evidence):
    initial, ckpt = L.load_checkpoint(parent)
    model_guard(L, initial, ckpt, game)
    L.validate_checkpoint(ckpt, dict(schema=game['schema'], rules=C.RULES, features=samples[0]['features']))
    model = L.ActorCritic(*ckpt['dimensions'])
    model.load_state_dict(initial.state_dict(), strict=True)
    C.require(all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items()), 'Parent initialization')
    selected = list(range(len(samples)))
    draws = T.Draws(samples, selected)
    drawn_groups = sorted({metadata['labels'][i]['episodeGroup'] for i in set(draws.indices)})
    eligible_counts = Counter(l['episodeGroup'] for l in metadata['labels'])
    distinct_counts = Counter(metadata['labels'][i]['episodeGroup'] for i in set(draws.indices))
    draw_counts = Counter(metadata['labels'][i]['episodeGroup'] for i in draws.indices)
    order = L.torch.randperm(6800, generator=L.torch.Generator().manual_seed(101)).tolist()
    selection = dict(selector=SELECTOR, selectedIndices=selected, expandedIndices=draws.indices, epochOrder=order,
                     orderSeed=101, actualDistinctDrawnLabels=len(set(draws.indices)),
                     groups=[dict(seed=g, eligibleLabels=eligible_counts[g], distinctDrawnLabels=distinct_counts[g],
                                  draws=draw_counts[g]) for g in sorted(eligible_counts)],
                     note='Cyclic prefix if labels exceed 6800; all eligible labels retained, no outcome ranking within positives.')
    evidence.json('selection.json', selection)
    evidence.json('input-metadata.json.gz', metadata, compress=True)
    provenance = dict(algorithm='BC', arm='naturalWins', config=C.CONFIG, configSHA256=C.digest(C.encoded(C.CONFIG)),
                      source=source, game=game, inputReviewStatus=REVIEW, limitations=LIMITATIONS,
                      parent=dict(path=str(parent), sha256=C.sha(parent), parameterSHA256=L.digest(initial)),
                      inputSHA256=metadata['inputSHA256'], manifestSHA256=metadata['manifestSHA256'],
                      eligibleEpisodeGroups=metadata['eligibleEpisodeGroups'], eligibleGroupCount=metadata['eligibleGroupCount'],
                      trainingEpisodeGroups=drawn_groups, trainingGroupCount=len(drawn_groups),
                      distinctDrawnLabels=len(set(draws.indices)),
                      positiveLabels=len(samples), positiveGames=metadata['positiveGames'], selector=SELECTOR,
                      metadataSHA256=C.sha(out / 'input-metadata.json.gz'), selectionSHA256=C.sha(out / 'selection.json'))
    before = T.accuracy(L, model, samples)
    model.train()
    L.torch.manual_seed(101)
    metrics, optimizer = L.bc_update(model, draws, epochs=1, lr=.0003, batch_size=34)
    C.require(draws.accesses == order and metrics['updates'] == 200 and metrics['samples'] == 6800, 'Actual BC order/budget')
    C.require(optimizer.state and all(int(v['step']) == 200 for v in optimizer.state.values()), 'Adam step count')
    C.require(all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items()
                  if k.startswith('critic.')), 'Critic head changed')
    model_guard(L, model, ckpt, game)
    model.eval()
    record = dict(initialParameterSHA256=L.digest(initial), parameterSHA256=L.digest(model), actualUpdates=200,
                  actualDraws=len(draws.accesses), orderSHA256=C.digest(C.encoded(draws.accesses)),
                  before=before, after=T.accuracy(L, model, samples), training=metrics, criticHeadUnchanged=True)
    path = out / 'naturalWins.pt'
    C.require(not path.exists() and not path.is_symlink(), 'Checkpoint already exists')
    L.save_checkpoint(path, model, ckpt['schema'], ckpt['rules'], {**provenance, 'metrics': record}, optimizer)
    C.require(path.stat().st_size <= 64 * MIB and evidence.used + path.stat().st_size <= evidence.limit, 'Checkpoint output limit')
    restored, saved = L.load_checkpoint(path)
    C.require(all(L.torch.equal(v, restored.state_dict()[k]) for k, v in model.state_dict().items()), 'Checkpoint roundtrip')
    C.require(saved['metadata'] == {**provenance, 'metrics': record}, 'Checkpoint metadata roundtrip')
    record.update(checkpoint='naturalWins.pt', checkpointSHA256=C.sha(path))
    return dict(status='complete', kind='natural-win-bc/v1', **provenance, result=record,
                torchVersion=str(L.torch.__version__))
