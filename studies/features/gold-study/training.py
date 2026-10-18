"""Same parent and draw order; original CPU ActorCritic and original bc_update."""
from collections import Counter
import io

from contracts import (C, CODE, T, CONFIG, LIMITATIONS, MODES, REVIEW, MIB,
                       load, model_guard, schema_dimensions)


def fit(L, parent, samples, metadata, source, game, evidence):
    warm_start = load('gold_warm_start', CODE / 'gold-features/warm_start.py').warm_start
    initial, original = L.load_checkpoint(parent)
    C.require(original['schema'] == game['schema'] and original['rules'] == C.RULES, 'Original parent schema/rules')
    schema = metadata['schema']
    baseline, ckpt = warm_start(original, L.ActorCritic)
    model_guard(L, baseline, ckpt, schema)
    initial_hash = L.digest(baseline)
    selected = list(range(len(samples['off'])))
    C.require(len(samples['off']) == len(samples['on']) == len(metadata['labels']) > 0, 'Paired labels')
    for off, on in zip(samples['off'], samples['on']):
        prefix = schema_dimensions(schema)[2]
        C.require(off['action'] == on['action'] and off['features']['candidates'] == on['features']['candidates'] and
                  off['features']['mask'] == on['features']['mask'] and
                  off['features']['global'][:prefix] == on['features']['global'][:prefix], 'Paired training data')
    order = L.torch.randperm(6800, generator=L.torch.Generator().manual_seed(101)).tolist()
    expansion = [i % len(selected) for i in range(6800)]
    selection = dict(selectedIndices=selected, expandedIndices=expansion, epochOrder=order,
                     orderSeed=101, episodeGroups=metadata['episodeGroups'])
    evidence.json('draw-plan.json', selection)
    provenance = dict(config=CONFIG, configSHA256=C.digest(C.encoded(CONFIG)), source=source, game=game,
        parent=dict(path=str(parent), sha256=C.sha(parent), parameterSHA256=L.digest(initial),
                    schema=original['schema'], dimensions=original['dimensions'],
                    metadata=original['metadata']), initialParameterSHA256=initial_hash,
        inputSHA256=metadata['inputSHA256'], inputReviewStatus=REVIEW, limitations=LIMITATIONS,
        metadataSHA256=C.sha(evidence.out / 'input-metadata.json'),
        drawPlanSHA256=C.sha(evidence.out / 'draw-plan.json'), schema=schema,
        positiveLabels=len(selected), episodeGroups=metadata['episodeGroups'])
    records = {}
    for name, mode in MODES.items():
        model, current = warm_start(original, L.ActorCritic)
        C.require(all(L.torch.equal(v, baseline.state_dict()[k]) for k, v in model.state_dict().items()),
                  'All initial parameters must be identical')
        L.validate_checkpoint(current, dict(schema=schema, rules=C.RULES, features=samples[mode][0]['features']))
        optimizer, actual_indices, actual_order = None, [], []
        before = T.accuracy(L, model, samples[mode]) if name != 'warmBaseline' else None
        metrics = None
        if name != 'warmBaseline':
            draws = T.Draws(samples[mode], selected)
            model.train()
            L.torch.manual_seed(101)  # After construction; original randperm is the only data shuffle.
            metrics, optimizer = L.bc_update(model, draws, epochs=1, lr=.0003, batch_size=34)
            C.require(draws.accesses == order and draws.indices == expansion and
                      metrics['updates'] == 200 and metrics['samples'] == 6800, 'Actual BC order/budget')
            C.require(optimizer.state and all(int(v['step']) == 200 for v in optimizer.state.values()), 'Adam updates')
            actual_order = draws.accesses
            actual_indices = [draws.indices[i] for i in actual_order]
        C.require(all(L.torch.equal(v, baseline.state_dict()[k]) for k, v in model.state_dict().items()
                      if k.startswith('critic.')), 'Critic head changed')
        if mode == 'off':
            C.require(L.torch.count_nonzero(model.state[0].weight[:, prefix:]).item() == 0, 'OFF new weights changed')
        model_guard(L, model, current, schema)
        counts = Counter(actual_indices)
        group_counts = Counter(metadata['labels'][i]['episodeGroup'] for i in actual_indices)
        model.eval()
        record = dict(mode=mode, initialParameterSHA256=initial_hash, parameterSHA256=L.digest(model),
            actualUpdates=0 if metrics is None else metrics['updates'], actualDraws=len(actual_indices),
            actualDistinctDrawnLabels=len(counts), actualLabelDrawCounts=[counts[i] for i in selected],
            actualGroupDrawCounts=[dict(episodeGroup=g, draws=group_counts[g]) for g in metadata['episodeGroups']],
            actualOrderSHA256=C.digest(C.encoded(actual_order)), criticHeadUnchanged=True,
            before=before, after=T.accuracy(L, model, samples[mode]) if metrics else None, training=metrics)
        md = dict(**provenance, arm=name, mode=mode, algorithm='warm-start' if metrics is None else 'BC',
                  visibleTrainingLabels=0 if metrics is None else len(selected),
                  visibleTrainingEpisodeGroups=[] if metrics is None else metadata['episodeGroups'],
                  features='old prefix + zero gold tail' if mode == 'off' else 'old prefix + actual before-action own gold',
                  goldWarmStart=current['metadata']['goldWarmStart'], metrics=record)
        # Exact original checkpoint format; exclusive bounded writer instead of L.save_checkpoint overwrite.
        with io.BytesIO() as stream:
            L.torch.save(dict(model=model.state_dict(), dimensions=model.dimensions, schema=schema, rules=C.RULES,
                             metadata=md, optimizer=optimizer.state_dict() if optimizer else None,
                             torch_rng=L.torch.get_rng_state()), stream)
            C.require(stream.tell() <= 64 * MIB, 'Checkpoint byte budget')
            identity = evidence.put(name + '.pt', stream.getvalue())
        restored, saved = L.load_checkpoint(evidence.out / (name + '.pt'))
        L.validate_checkpoint(saved, dict(schema=schema, rules=C.RULES, features=samples[mode][0]['features']))
        C.require(all(L.torch.equal(v, restored.state_dict()[k]) for k, v in model.state_dict().items()) and
                  saved['metadata'] == md, 'Standard checkpoint roundtrip')
        records[name] = {**record, 'checkpointSHA256': identity['sha256']}
    return dict(status='complete', kind='gold-study/v1', **provenance, models=records,
                evidence=dict(evidence.files), torchVersion=str(L.torch.__version__))
