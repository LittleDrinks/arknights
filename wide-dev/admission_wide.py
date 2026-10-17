"""Only completed F steps3000; externally supplied hashes precede deserialization."""
from collections import Counter
from pathlib import Path
import re

from contracts_wide import C, F, FT, P, Guard, MIB, digest, require, safe


def sha_argument(value):
    require(re.fullmatch('[0-9a-f]{64}', value) is not None, 'Explicit lowercase SHA256 required')
    return value


def model_identity(L, model, ckpt, game):
    F.model_guard(L, model, ckpt, game)
    require(not model.training and all(not p.requires_grad for p in model.parameters()), 'Frozen inference model required')
    return dict(parameterSHA256=L.digest(model), parameterSchema=P.parameter_schema(model),
                checkpointMetadataSHA256=C.digest(C.encoded(ckpt['metadata'])),
                schema=ckpt['schema'], rules=ckpt['rules'], dimensions=ckpt['dimensions'])


def proof_identity(identity):
    # The full schema is in admission.json once; do not repeat it 4096 times.
    return dict(parameterSHA256=identity['parameterSHA256'], identitySHA256=C.digest(C.encoded(identity)))


def report_contract(report, fit_source, game, pin, parent):
    expected = dict(status='complete', kind='bc-fit-budget/v1', creditGranted=True,
                    source=fit_source, game=game, gameIdentity=pin, config=F.CONFIG,
                    configSHA256=C.digest(C.encoded(F.CONFIG)), manifestSHA256=F.MANIFEST_SHA,
                    positiveLabels=498, positiveGames=6, schema=game['schema'], limitations=F.LIMITATIONS)
    require(all(report.get(k) == v for k, v in expected.items()), 'Completed original F training report required')
    require(report.get('startedAt') and report.get('finishedAt') and set(report['models']) == set(F.ARMS),
            'Completed two-arm training evidence required (only steps3000 is evaluated)')
    require(report['parent']['path'] == str(parent) and report['parent']['sha256'] == F.PARENT_SHA,
            'Designated parent provenance')
    require(report['finalChecks'] == dict(sourceSHA256=fit_source['sha256'], parentSHA256=F.PARENT_SHA,
            gameIdentity=pin, staticGame=game, outputDirectoryUnchanged=True,
            inputEvidenceVerified=True, publishedEvidenceVerified=True), 'Missing original training final checks')


def admit(L, trained, parent, candidate_sha, report_sha, fit_source, game, pin):
    trained = safe(trained)
    require(trained.name == 'bc-fit-budget-train-v1', 'Only completed bc-fit-budget-train-v1')
    guard = Guard()
    report_id = guard.file(trained / 'train.json')
    candidate_id = guard.file(trained / 'steps3000.pt')
    require(report_id['sha256'] == sha_argument(report_sha) and
            candidate_id['sha256'] == sha_argument(candidate_sha), 'Explicit train/checkpoint SHA mismatch')
    # Neither a filename nor status=complete is a training attestation.
    report = F.read_json(trained / 'train.json')
    report_contract(report, fit_source, game, pin, parent)
    expected_names = {'admission-paths.json', 'recapture-manifest.json', 'source-manifest.json',
                      'source-selection.json', 'input-metadata.json', 'draw-plan.json'}
    expected_names |= {f'old-{i}.json.gz' for i in range(6)}
    expected_names |= {f'new-{i}-{mode}.json.gz' for i in range(6) for mode in ('off', 'on')}
    expected_names |= {arm + suffix for arm in F.ARMS for suffix in ('.pt', '-accesses.json')}
    require(set(report['evidence']) == expected_names and
            {p.name for p in trained.iterdir()} == expected_names | {'train.json'}, 'Complete training evidence set')
    guard.directory(trained)
    for name in expected_names:
        require(guard.file(trained / name) == report['evidence'][name], 'Training evidence SHA/size: ' + name)

    metadata = F.read_json(trained / 'input-metadata.json')
    paths = F.read_json(trained / 'admission-paths.json')
    require(guard.file(trained / 'input-metadata.json')['sha256'] == report['metadataSHA256'] and
            guard.file(trained / 'draw-plan.json')['sha256'] == report['drawPlanSHA256'] and
            guard.file(trained / 'recapture-manifest.json')['sha256'] == F.MANIFEST_SHA, 'Training input identities')
    require(metadata['inputSHA256'] == report['inputSHA256'] == C.digest(C.encoded(metadata['inputFiles'])) and
            metadata['inputReviewStatus'] == report['inputReviewStatus'] == F.N.REVIEW and
            metadata['episodeGroups'] == report['episodeGroups'] and len(set(report['episodeGroups'])) == 6 and
            len(metadata['labels']) == 498 and metadata['positiveLabels'] == 498 and metadata['positiveGames'] == 6,
            '498-label / six-game input provenance')
    require(metadata['recapture'] == F.read_json(trained / 'recapture-manifest.json'), 'Recapture metadata binding')
    for path in paths:
        guard.path(path)
    for path in metadata['inputFiles']:
        require(Path(path).is_absolute(), 'Absolute input binding')
        guard.file(path)
    for path in metadata['rawFileSets']:
        guard.path(path)
    F.recheck_inputs(metadata, paths)
    # Original F checks the frozen draw plan and actual accesses, not report claims alone.
    plan = F.read_json(trained / 'draw-plan.json')
    orders = FT.epoch_orders(L, 15)
    require(plan == dict(selectedIndices=list(range(498)), expandedIndices=[i % 498 for i in range(6800)],
                        epochOrders=orders, orderSeed=101, epochs=F.ARMS), 'Original continuous-RNG draw plan')
    actual = F.read_json(trained / 'steps3000-accesses.json')
    accesses = [i for epoch in orders for i in epoch]
    labels = [i % 498 for i in accesses]
    counts = Counter(labels)
    groups = Counter(metadata['labels'][i]['episodeGroup'] for i in labels)
    record = report['models']['steps3000']
    require(actual == dict(accesses=accesses, labelIndices=labels) and
            record['accessesSHA256'] == guard.file(trained / 'steps3000-accesses.json')['sha256'] and
            record['actualOrderSHA256'] == C.digest(C.encoded(accesses)) and
            record['actualDistinctDrawnLabels'] == 498 and
            record['actualLabelDrawCounts'] == [counts[i] for i in range(498)] and
            record['actualGroupDrawCounts'] == [dict(episodeGroup=g, draws=groups[g]) for g in metadata['episodeGroups']],
            'Actual 102000 accesses/labels/groups')
    require(record['checkpointSHA256'] == candidate_sha and record['actualUpdates'] == 3000 and
            record['actualDraws'] == 102000 and record['epochs'] == 15 and
            record['training']['updates'] == 3000 and record['training']['samples'] == 6800,
            'Actual steps3000 budget')
    guard.file(parent)
    loaded = {name: L.load_checkpoint(path) for name, path in
              (('parent', parent), ('steps3000', trained / 'steps3000.pt'))}
    identities = {}
    for name, (model, ckpt) in loaded.items():
        require(set(ckpt) == {'model', 'dimensions', 'schema', 'rules', 'metadata', 'optimizer', 'torch_rng'},
                'Original standard checkpoint keys')
        F.model_guard(L, model, ckpt, game)
        model.eval()
        model.requires_grad_(False)
        identities[name] = model_identity(L, model, ckpt, game)
    initial, parent_ckpt = loaded['parent']
    model, ckpt = loaded['steps3000']
    require(report['parent'] == dict(path=str(parent), **guard.file(parent), parameterSHA256=L.digest(initial),
            dimensions=parent_ckpt['dimensions'], schema=parent_ckpt['schema'], metadata=parent_ckpt['metadata']),
            'Full parent parameters/metadata')
    require(record['initialParameterSHA256'] == L.digest(initial) and
            record['parameterSHA256'] == L.digest(model) != L.digest(initial), 'Trained candidate parameters')
    provenance = ('config', 'configSHA256', 'source', 'game', 'gameIdentity', 'parent', 'manifestSHA256',
                  'inputSHA256', 'schema', 'inputReviewStatus', 'limitations', 'metadataSHA256', 'drawPlanSHA256',
                  'positiveLabels', 'positiveGames', 'episodeGroups')
    require(ckpt['metadata'] == dict(**{k: report[k] for k in provenance}, algorithm='BC', arm='steps3000',
            features='OFF global[:5267]; unchanged candidates/mask',
            metrics={k: v for k, v in record.items() if k != 'checkpointSHA256'}), 'Checkpoint training provenance')
    critic = FT.critic_sha(L, initial)
    require(record['criticHeadUnchanged'] is True and
            record['initialCriticHeadSHA256'] == record['finalCriticHeadSHA256'] == critic == FT.critic_sha(L, model) and
            all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items() if k.startswith('critic.')),
            'Original critic head tensor identity')
    adam = record['adamSteps']
    require(adam == {n: 3000 for n, _ in model.named_parameters() if not n.startswith('critic.')}, 'Actual Adam steps')
    optimizer = ckpt['optimizer']
    require(len(optimizer['param_groups']) == 1 and optimizer['param_groups'][0]['lr'] == .0003 and
            len(optimizer['state']) == len(adam), 'Saved original Adam state')
    ids = optimizer['param_groups'][0]['params']
    parameters = list(model.named_parameters())
    require(len(ids) == len(parameters) and len(set(ids)) == len(ids), 'Adam parameter mapping')
    require(set(optimizer['state']) == {ident for ident, (name, _) in zip(ids, parameters) if name in adam},
            'Adam actor parameter identities')
    for ident, (name, param) in zip(ids, parameters):
        if name not in adam:
            continue
        state = optimizer['state'][ident]
        require(float(state['step']) == 3000 and all(v in state and state[v].shape == param.shape and
                L.torch.isfinite(state[v]).all().item() for v in ('exp_avg', 'exp_avg_sq')), 'Saved Adam tensor/steps')
    # Immutable deep JSON snapshot; checkpoint dictionaries are mutable objects.
    identities = C.parse(C.encoded(identities))

    def verify(deadline):
        guard.verify(deadline)
        F.recheck_inputs(metadata, paths)
        for name, (frozen, checkpoint) in loaded.items():
            require(model_identity(L, frozen, checkpoint, game) == identities[name], 'Frozen model/metadata drift')
        require(FT.critic_sha(L, loaded['steps3000'][0]) == critic, 'Critic drift')

    verify(float('inf'))
    return loaded, dict(train=report_id, candidate=candidate_id, models=identities,
                        inputSHA256=metadata['inputSHA256'], inputReviewStatus=metadata['inputReviewStatus']), verify
