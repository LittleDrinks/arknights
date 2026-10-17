"""Three data sources, one original L architecture and one fixed optimizer budget."""
from common import ARMS, CONFIG, LIMITATIONS, digest, encoded, require, sha, write


class Draws:
    """Reference-only cyclic expansion; distinguish BC validation iteration from training indexing."""
    def __init__(self, samples, selected):
        self.samples = samples
        self.indices = [selected[i % len(selected)] for i in range(CONFIG['draws'])]
        self.accesses = []

    def __len__(self):
        return len(self.indices)

    def __iter__(self):
        return (self.samples[i] for i in self.indices)

    def __getitem__(self, i):
        self.accesses.append(i)
        return self.samples[self.indices[i]]


def accuracy(L, model, samples):
    correct, loss = 0, 0.
    with L.torch.no_grad():
        for start in range(0, len(samples), 34):
            batch = samples[start:start + 34]
            dist, _ = model([s['features'] for s in batch])
            labels = L.torch.tensor([s['action'] for s in batch])
            correct += (dist.logits.argmax(-1) == labels).sum().item()
            loss += -dist.log_prob(labels).sum().item()
    return {'accuracy': correct / len(samples), 'loss': loss / len(samples), 'uniqueLabels': len(samples)}


def train(L, parent_path, admitted, out, source, game):
    parent_hash = sha(parent_path)
    initial, ckpt = L.load_checkpoint(parent_path)
    require(ckpt['schema'] == game['schema'] and ckpt['rules'] == game['rules'], 'Parent schema/rules')
    L.validate_checkpoint(ckpt, {'schema': game['schema'], 'rules': game['rules'],
                                'features': admitted['samples']['primary68'][0]['features']})
    require(all(L.torch.isfinite(v).all().item() for v in initial.state_dict().values()), 'Nonfinite parent model')
    config_hash = digest(encoded(CONFIG))
    order = L.torch.randperm(6800, generator=L.torch.Generator().manual_seed(CONFIG['orderSeed'])).tolist()
    provenance = {k: v for k, v in admitted.items() if k != 'samples'}
    write(out / 'input-metadata.json', provenance)
    write(out / 'selection.json', {'indices': admitted['selections'], 'strata': admitted['strata'],
                                 'selectionSeed': CONFIG['selectionSeed'], 'epochOrder': order})
    report = {'status': 'running', 'kind': 'single-seen-win-data-source-ablation/v1', 'config': CONFIG,
              'configSHA256': config_hash, 'source': source, 'game': game, 'limitations': LIMITATIONS,
              'inputReviewStatus': admitted['inputReviewStatus'], 'rawInputSHA256': admitted['rawInputSHA256'],
              'parent': {'path': str(parent_path), 'sha256': parent_hash, 'parameterSHA256': L.digest(initial)},
              'metadataSHA256': sha(out / 'input-metadata.json'), 'selectionSHA256': sha(out / 'selection.json'),
              'torchVersion': str(L.torch.__version__), 'arms': {}}
    write(out / 'train.json', report)
    try:
        for arm in ARMS:
            model = L.ActorCritic(*ckpt['dimensions'])
            model.load_state_dict(initial.state_dict(), strict=True)
            require(all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items()), 'Initial full model mismatch')
            draws = Draws(admitted['samples'][arm], admitted['selections'][arm])
            unique = [admitted['samples'][arm][i] for i in admitted['selections'][arm]]
            before = accuracy(L, model, unique)
            model.train()
            L.torch.manual_seed(CONFIG['orderSeed'])  # AFTER model construction and before original bc_update.
            metrics, optimizer = L.bc_update(model, draws, epochs=1, lr=.0003, batch_size=34)
            require(draws.accesses == order and metrics['updates'] == 200 and metrics['samples'] == 6800,
                    'Actual BC order/updates/draws mismatch')
            require(optimizer.state and all(int(v['step']) == 200 for v in optimizer.state.values()), 'Adam step mismatch')
            require(all(L.torch.equal(v, initial.state_dict()[k]) for k, v in model.state_dict().items()
                        if k.startswith('critic.')), 'BC unexpectedly changed critic head')
            model.eval()
            record = {'initialParameterSHA256': L.digest(initial), 'parameterSHA256': L.digest(model),
                      'actualUpdates': metrics['updates'], 'actualDraws': len(draws.accesses),
                      'orderSHA256': digest(encoded(draws.accesses)), 'expandedIndices': draws.indices,
                      'selectedIndices': admitted['selections'][arm], 'before': before,
                      'after': accuracy(L, model, unique), 'training': metrics}
            path = out / (arm + '.pt')
            require(not path.exists(), 'Checkpoint already exists')
            L.save_checkpoint(path, model, ckpt['schema'], ckpt['rules'],
                              {'algorithm': 'BC', 'arm': arm, 'config': CONFIG, 'configSHA256': config_hash,
                               'source': source, 'parent': report['parent'], 'rawInputSHA256': admitted['rawInputSHA256'],
                               'metadataSHA256': report['metadataSHA256'], 'selectionSHA256': report['selectionSHA256'],
                               'episodeGroup': admitted['metadata']['episodeGroup'], 'limitations': LIMITATIONS,
                               'inputReviewStatus': admitted['inputReviewStatus'], 'metrics': record}, optimizer)
            restored, _ = L.load_checkpoint(path)
            require(all(L.torch.equal(v, restored.state_dict()[k]) for k, v in model.state_dict().items()), 'Checkpoint roundtrip')
            record.update(checkpoint=path.name, checkpointSHA256=sha(path))
            report['arms'][arm] = record
            write(out / 'train.json', report)
        require(sha(parent_path) == parent_hash, 'Parent changed during training')
        report['status'] = 'complete'
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        write(out / 'train.json', report)
    return report
