"""Independent original-parent BC arms; no feature cache or alternative architecture."""
from collections import Counter
import io

from contracts_fit import (C, T, ARMS, CONFIG, MANIFEST_SHA, MIB, LIMITATIONS,
                           identity, model_guard)


def epoch_orders(L, epochs):
    generator = L.torch.Generator().manual_seed(101)
    return [L.torch.randperm(6800, generator=generator).tolist() for _ in range(epochs)]


def critic_sha(L, model):
    return C.digest(L.torch.cat([v.detach().reshape(-1) for n, v in model.state_dict().items()
                                if n.startswith('critic.')]).numpy().tobytes())


def train_arm(L, initial, samples, epochs):
    model = L.ActorCritic(*initial.dimensions)
    model.load_state_dict(initial.state_dict(), strict=True)
    C.require(all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items()), 'Exact parent start')
    draws = T.Draws(samples, list(range(len(samples))))
    orders = epoch_orders(L, epochs)
    before = T.accuracy(L, model, samples)
    model.train()
    L.torch.manual_seed(101)  # Construction/accuracy precede the one training RNG reset.
    metrics, optimizer = L.bc_update(model, draws, epochs=epochs, lr=.0003, batch_size=34)
    C.require(draws.accesses == [i for order in orders for i in order] and
              draws.indices == [i % len(samples) for i in range(6800)] and
              metrics['updates'] == epochs * 200 and metrics['samples'] == 6800,
              'Original BC actual order / update budget')
    adam = {name: int(optimizer.state[p]['step']) for name, p in model.named_parameters() if p in optimizer.state}
    C.require(adam and set(adam) == {n for n, _ in model.named_parameters() if not n.startswith('critic.')} and
              all(v == epochs * 200 for v in adam.values()), 'Actual Adam state/steps')
    C.require(all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items()
                  if k.startswith('critic.')), 'Critic head changed')
    model.eval()
    record = dict(initialParameterSHA256=L.digest(initial), parameterSHA256=L.digest(model),
        actualUpdates=metrics['updates'], actualDraws=len(draws.accesses), adamSteps=adam,
        criticHeadUnchanged=True, before=before, after=T.accuracy(L, model, samples), training=metrics,
        initialCriticHeadSHA256=critic_sha(L, initial), finalCriticHeadSHA256=critic_sha(L, model),
        actualOrderSHA256=C.digest(C.encoded(draws.accesses)))
    return model, optimizer, record, draws


def fit(L, parent, samples, metadata, source, game, game_pin, evidence):
    initial, ckpt = L.load_checkpoint(parent)
    model_guard(L, initial, ckpt, game)
    L.validate_checkpoint(ckpt, dict(schema=game['schema'], rules=C.RULES, features=samples[0]['features']))
    orders = epoch_orders(L, 15)
    plan = dict(selectedIndices=list(range(498)), expandedIndices=[i % 498 for i in range(6800)],
                epochOrders=orders, orderSeed=101, epochs=ARMS)
    evidence.json('draw-plan.json', plan)
    provenance = dict(config=CONFIG, configSHA256=C.digest(C.encoded(CONFIG)), source=source,
        game=game, gameIdentity=game_pin, parent=dict(path=str(parent), **identity(parent),
            parameterSHA256=L.digest(initial), dimensions=ckpt['dimensions'], schema=ckpt['schema'], metadata=ckpt['metadata']),
        manifestSHA256=MANIFEST_SHA, inputSHA256=metadata['inputSHA256'], schema=game['schema'],
        inputReviewStatus=metadata['inputReviewStatus'], limitations=LIMITATIONS,
        metadataSHA256=identity(evidence.out / 'input-metadata.json')['sha256'],
        drawPlanSHA256=identity(evidence.out / 'draw-plan.json')['sha256'],
        positiveLabels=498, positiveGames=6, episodeGroups=metadata['episodeGroups'])
    records = {}
    for name, epochs in ARMS.items():
        model, optimizer, record, draws = train_arm(L, initial, samples, epochs)
        model_guard(L, model, ckpt, game)
        actual = [draws.indices[i] for i in draws.accesses]
        counts = Counter(actual)
        groups = Counter(metadata['labels'][i]['episodeGroup'] for i in actual)
        evidence.json(name + '-accesses.json', dict(accesses=draws.accesses, labelIndices=actual))
        record.update(epochs=epochs, actualDistinctDrawnLabels=len(counts),
            actualLabelDrawCounts=[counts[i] for i in range(498)],
            actualGroupDrawCounts=[dict(episodeGroup=g, draws=groups[g]) for g in metadata['episodeGroups']],
            accessesSHA256=identity(evidence.out / (name + '-accesses.json'))['sha256'])
        md = dict(**provenance, algorithm='BC', arm=name, features='OFF global[:5267]; unchanged candidates/mask', metrics=record)
        # Exact L.save_checkpoint schema; exclusive bounded evidence writer.
        with io.BytesIO() as stream:
            L.torch.save(dict(model=model.state_dict(), dimensions=model.dimensions, schema=ckpt['schema'],
                rules=ckpt['rules'], metadata=md, optimizer=optimizer.state_dict(), torch_rng=L.torch.get_rng_state()), stream)
            C.require(stream.tell() <= 64 * MIB, 'Checkpoint byte budget')
            saved_identity = evidence.put(name + '.pt', stream.getvalue())
        restored, saved = L.load_checkpoint(evidence.out / (name + '.pt'))
        L.validate_checkpoint(saved, dict(schema=game['schema'], rules=C.RULES, features=samples[0]['features']))
        C.require(all(L.torch.equal(v, restored.state_dict()[k]) for k, v in model.state_dict().items()) and
                  saved['metadata'] == md, 'Standard checkpoint roundtrip')
        records[name] = {**record, 'checkpointSHA256': saved_identity['sha256']}
    return dict(status='complete', kind='bc-fit-budget/v1', **provenance, models=records,
                evidence=dict(evidence.files), torchVersion=str(L.torch.__version__))
