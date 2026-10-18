"""Fixed data-scale arms using the original F/T trainer and original L checkpoint."""
from collections import Counter
import gzip
import io

from core import C, CONFIG, MIB, PARENT_SHA, REFERENCE_SHA, load_fit, require
from admission_scale import arm_labels, exact


def parameter_shas(L, model):
    require(len(list(model.named_parameters())) == len(model.state_dict()) == 8, 'Eight original parameters')
    return {name: C.digest(value.detach().cpu().contiguous().numpy().tobytes())
            for name, value in model.state_dict().items()}


def model_guard(L, model, ckpt, game):
    require(type(model) is L.ActorCritic and model.dimensions == ckpt['dimensions'] == [5267, 419, 128],
            'Frozen standard original architecture')
    require(ckpt['schema'] == game['schema'] and ckpt['rules'] == C.RULES, 'Model schema/rules')
    require(L.torch.get_default_dtype() == L.torch.float32 and all(
        p.device.type == 'cpu' and p.dtype == L.torch.float32 and L.torch.isfinite(p).all().item()
        for p in model.state_dict().values()), 'Finite original CPU float32')
    parameter_shas(L, model)


def counts(labels, actual):
    require(len(labels) <= 6800 and len(labels) > 0, 'Retain every eligible label')
    per_label = Counter(actual)
    require(set(per_label) == set(range(len(labels))), 'Every eligible label must actually be drawn')
    groups = sorted({r['episodeGroup'] for r in labels})
    pools = sorted({r['pool'] for r in labels})
    games = {(r['corpus'], r['seed'], r['pool']) for r in labels}
    result = dict(positiveLabels=len(labels), positiveGames=len(games), episodeGroupCount=len(groups),
                  actualDistinctDrawnLabels=len(per_label), actualLabelDrawCounts=[per_label[i] for i in range(len(labels))])
    for name, values, key in (('groups', groups, 'episodeGroup'), ('pools', pools, 'pool')):
        eligible, drawn = Counter(), Counter()
        grouped_games = {value: set() for value in values}
        for i, row in enumerate(labels):
            eligible[row[key]] += 1
            drawn[row[key]] += per_label[i]
            grouped_games[row[key]].add((row['corpus'], row['seed'], row['pool']))
        result[name] = [dict(value=value, labels=eligible[value], draws=drawn[value],
                            games=len(grouped_games[value])) for value in values]
    return result


def original_train(L, initial, samples):
    require(0 < len(samples) <= 6800, 'No cyclic prefix truncation')
    require(all(set(s) == {'features', 'action'} for s in samples), 'Actor input keys')
    return load_fit().train_arm(L, initial, samples, 15)


def save_standard(L, model, optimizer, ckpt, metadata, evidence, name):
    with io.BytesIO() as stream:
        # Identical field schema to L.save_checkpoint; exclusive bounded publication.
        L.torch.save(dict(model=model.state_dict(), dimensions=model.dimensions, schema=ckpt['schema'],
                         rules=ckpt['rules'], metadata=metadata, optimizer=optimizer.state_dict(),
                         torch_rng=L.torch.get_rng_state()), stream)
        require(stream.tell() <= 64*MIB, 'Checkpoint size bound')
        identity = evidence.put(name+'.pt', stream.getvalue())
    restored, saved = L.load_checkpoint(evidence.out / (name+'.pt'))
    require(L.digest(restored) == L.digest(model) and parameter_shas(L, restored) == parameter_shas(L, model) and
            all(L.torch.equal(v, restored.state_dict()[k]) for k, v in model.state_dict().items()),
            'All eight parameters must roundtrip exactly')
    exact(saved['metadata'], metadata, 'Checkpoint metadata roundtrip')
    require(set(saved) == {'model', 'dimensions', 'schema', 'rules', 'metadata', 'optimizer', 'torch_rng'},
            'Standard L checkpoint keys')
    return identity, saved


def train(L, parent, ref_dir, samples, metadata, game, evidence, provenance, names):
    initial, ckpt = L.load_checkpoint(parent)
    model_guard(L, initial, ckpt, game)
    initial.eval()
    initial.requires_grad_(False)
    initial_sha, initial_tensors = L.digest(initial), parameter_shas(L, initial)
    reference, reference_ckpt = L.load_checkpoint(ref_dir / 'steps3000.pt')
    model_guard(L, reference, reference_ckpt, game)
    ref_tensors = parameter_shas(L, reference)
    require(L.digest(reference) == metadata['referenceReport']['models']['steps3000']['parameterSHA256'],
            'Historical checkpoint/report parameter identity')
    del reference
    F = load_fit()
    orders = F.epoch_orders(L, 15)
    # The historical report binds this file; compare exact full order, not just RNG seed.
    historical = C.parse(gzip.decompress((evidence.out / 'historical-draw-plan.json.gz').read_bytes()))
    exact(orders, historical['epochOrders'], 'Historical continuous seed101 epoch order')
    labels = arm_labels(metadata)
    evidence.json('draw-plan.json.gz', dict(config=CONFIG, epochOrders=orders,
        arms={name: dict(selectedIndices=list(range(len(samples[name]))),
                        expandedIndices=[i % len(samples[name]) for i in range(6800)],
                        labels=labels[name]) for name in names}), compress=True)
    records = {}
    for name in names:
        L.validate_checkpoint(ckpt, dict(schema=game['schema'], rules=C.RULES, features=samples[name][0]['features']))
        model, optimizer, record, draws = original_train(L, initial, samples[name])
        model_guard(L, model, ckpt, game)
        require(record['actualUpdates'] == 3000 and record['actualDraws'] == 102000 and
                record['initialParameterSHA256'] == initial_sha, 'Equal fixed optimization budget / parent')
        actual = [draws.indices[i] for i in draws.accesses]
        tensor_shas = parameter_shas(L, model)
        if name == 'old6Reference':
            require(tensor_shas == ref_tensors, 'Old-six reproduction differs from frozen steps3000 parameters')
            record.update(referenceCheckpointSHA256=REFERENCE_SHA, referenceEightParametersExact=True,
                          checkpointFileEqualityClaimed=False)
        record.update(**counts(labels[name], actual), parameterSHA256= L.digest(model),
                      eightParameterSHA256=tensor_shas, labelOrderSHA256=C.digest(C.encoded(actual)),
                      sampleOrderSHA256=C.digest(C.encoded(labels[name])))
        access_id = evidence.json(name+'-accesses.json.gz', dict(accesses=draws.accesses, labelIndices=actual), compress=True)
        record['accessesFile'] = access_id
        md = dict(kind='scale-bc/v1', algorithm='BC', arm=name, config=CONFIG,
                  **provenance, parentSHA256=PARENT_SHA, initialParameterSHA256=initial_sha,
                  drawPlanSHA256=evidence.files['draw-plan.json.gz']['sha256'], metrics=record)
        identity, saved = save_standard(L, model, optimizer, ckpt, md, evidence, name)
        L.validate_checkpoint(saved, dict(schema=game['schema'], rules=C.RULES, features=samples[name][0]['features']))
        records[name] = {**record, 'checkpoint': name+'.pt', 'checkpointIdentity': identity}
        require(L.digest(initial) == initial_sha and parameter_shas(L, initial) == initial_tensors,
                'Frozen parent changed')
        del model, optimizer, draws, saved
    return dict(models=records, initialParameterSHA256=initial_sha, initialEightParameterSHA256=initial_tensors,
                torchVersion=str(L.torch.__version__), config=CONFIG)
