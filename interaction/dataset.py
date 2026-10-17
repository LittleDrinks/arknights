"""Whole-file admission for the original interleaved demo JSONL and full-terminal v1."""
from collections import Counter
import hashlib
from pathlib import Path

from common import (RULES, json_bytes, require, schema_dimensions, sha, strict_json, validate_features, validate_label)

ERROR_KEYS = ('engineErrors', 'actionErrors', 'controllerErrors')
COUNTERS = (*ERROR_KEYS, 'rounds', 'steps', 'budgetForcedReady', 'controllerBlockedSales')


def identity(row):
    require(type(row.get('seed')) is int and 1 <= row['seed'] <= 0xffffffff, 'Episode seed')
    require(row.get('difficulty') in ('NORMAL', 'HARD', 'ABYSS'), 'Episode difficulty')
    return (row['difficulty'], row['seed'])


def full_terminal(terminal, expected, schema):
    """Terminal used for admission/reporting only; never passed to a model."""
    require(isinstance(terminal, dict) and {'schema', 'rules', 'episode', 'observation', 'candidates', 'features',
            'reward', 'done', 'victory', 'rounds', 'steps', 'summary', 'controller', *COUNTERS} <= set(terminal),
            'Full env.view terminal required, not a summary excerpt')
    require(terminal.get('done') is True, 'Incomplete terminal')
    require(terminal.get('schema') == schema and terminal.get('rules') == RULES, 'Terminal schema/rules')
    require(terminal.get('episode') == expected, 'Terminal episode identity')
    for key in COUNTERS:
        require(type(terminal.get(key)) is int and terminal[key] >= 0, 'Terminal counter: ' + key)
    require(all(terminal[k] == 0 for k in ERROR_KEYS), 'Terminal has errors')
    require(type(terminal.get('victory')) is bool, 'Terminal victory')
    require(terminal['reward'] == (1 if terminal['victory'] else -1), 'Terminal reward/outcome mismatch')
    require(terminal['controller'] == 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1',
            'Terminal controller mismatch')
    require(isinstance(terminal['observation'], dict) and
            {'own', 'public', 'history'} <= set(terminal['observation']), 'Missing complete terminal observation')
    summary = terminal.get('summary')
    require(isinstance(summary, dict) and bool(summary) and
            summary.get('victory') is terminal['victory'] and
            summary.get('roundsPassed') == terminal['rounds'] and
            summary.get('seed') == expected['seed'] and summary.get('difficulty') == expected['difficulty'],
            'Missing/inconsistent real terminal summary')
    require(isinstance(summary.get('reason'), str) and isinstance(summary.get('players'), list) and
            len(summary['players']) == 1 and isinstance(summary['players'][0], dict) and
            summary['players'][0].get('playerId') == 'learner', 'Incomplete solo terminal summary')
    require(terminal.get('candidates') == [] and terminal.get('features', {}).get('candidates') == [] and
            terminal.get('features', {}).get('mask') == [], 'Terminal still has candidates')
    validate_features(terminal['features'], schema_dimensions(schema), terminal=True)


def load_dataset(data_path, manifest_path, *, allow_legacy=False, synthetic=False, expected_schema=None):
    manifest = strict_json(Path(manifest_path).read_text())
    require(manifest.get('format') == 'interaction-dataset/v1' and manifest.get('split') == 'train',
            'Only explicitly declared training data is admitted')
    schema = manifest['schema']
    dims = schema_dimensions(schema)
    require(manifest.get('rules') == RULES, 'Dataset rules mismatch')
    if expected_schema is not None:
        require(schema == expected_schema, 'Dataset differs from pinned game schema')
    kind = manifest.get('recordFormat')
    require(kind in ('legacy-demo', 'full-terminal-v1'), 'Unknown record format')
    require(kind != 'legacy-demo' or allow_legacy, 'Legacy requires --allow-legacy-metadata')
    trust = manifest.get('provenance', {})
    require(all(isinstance(trust.get(k), str) and trust[k].strip() for k in
                ('collector', 'reviewedBy', 'trainingSplitEvidence', 'terminalAdmission')),
            'Explicit collection/split/admission provenance required')
    source_hashes = manifest.get('sourceSha256')
    require(isinstance(source_hashes, dict) and bool(source_hashes) and
            all(isinstance(v, str) and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
                for v in source_hashes.values()), 'Source hashes required')
    expected = {}
    for episode in manifest['episodes']:
        key = identity(episode)
        require(key not in expected, 'Duplicate episode identity')
        require(episode.get('split') == 'train', 'Validation/test/dev episodes cannot train')
        require(episode.get('mode') == 'solo' and type(episode.get('budget')) is int and
                1 <= episode['budget'] <= 1024, 'Episode mode/budget')
        require(type(episode.get('samples')) is int and episode['samples'] >= 0, 'Episode sample count')
        require(type(episode.get('victory')) is bool, 'Expected episode outcome required')
        expected[key] = episode
    require(bool(expected), 'No declared episodes')
    require(sum(e['samples'] for e in expected.values()) == manifest.get('sampleCount'), 'Manifest sample count')
    require(len(expected) == manifest.get('episodeCount'), 'Manifest episode count')
    if synthetic:
        require(schema == {'version': 'shop-v1', 'ids': [], 'bonds': [], 'bands': []} and
                manifest.get('teacher') == 'synthetic-contract' and
                0 < manifest['sampleCount'] <= 128 and len(expected) <= 16, 'Small synthetic data only')
    else:
        require(manifest['sampleCount'] == 16014 and len(expected) == 192, 'Original 16014 labels / 192 demos only')
        require(manifest.get('teacher') == 'upstream-bot/rehearsal=0', 'Original teacher required')
    # Hash the SAME bytes we parse, through EOF. There is no max-samples early exit.
    digest = hashlib.sha256()
    seen, closed, rounds, counts = set(), set(), {}, Counter()
    samples, order, header = [], hashlib.sha256(), None
    wins = Counter()
    with open(data_path, 'rb') as stream:
        for line_no, raw in enumerate(stream, 1):
            require(len(raw) <= 8 * 1024 * 1024 and raw.endswith(b'\n'), f'Truncated/oversize line {line_no}')
            digest.update(raw)
            row = strict_json(raw)
            require(isinstance(row, dict), 'JSONL row must be object')
            if row.get('type') == 'metadata':
                require(header is None and line_no == 1, 'Metadata must occur once at start')
                require(row.get('split', 'train') == 'train', 'Non-training metadata')
                require(row.get('schema') == schema and row.get('rules') == RULES and
                        row.get('teacher') == manifest['teacher'], 'Header schema/rules/teacher mismatch')
                if 'sourceSha256' in row:
                    require(row['sourceSha256'] == source_hashes, 'Inherited source hashes mismatch')
                header = row
                continue
            require(header is not None, 'Missing metadata')
            require(row.get('type') in ('sample', 'episode'), 'Unexpected row type')
            key = identity(row)
            require(key in expected and key not in closed, 'Unknown or already closed episode')
            ep = expected[key]
            require(row.get('split', 'train') == 'train', 'Non-training row')
            seen.add(key)
            if row['type'] == 'sample':
                require(set(row) <= {'type', 'seed', 'difficulty', 'victory', 'round', 'features', 'action', 'split'},
                        'Unknown sample field; terminal/future payload forbidden')
                validate_label(row, dims)
                require(type(row.get('round')) is int and row['round'] >= max(1, rounds.get(key, 1)),
                        'Sample rounds must be positive and monotonic within episode')
                require(row.get('victory', ep['victory']) is ep['victory'], 'Sample outcome metadata mismatch')
                rounds[key] = row['round']
                counts[key] += 1
                require(counts[key] <= ep['samples'], 'Too many samples for episode')
                # No seed, outcome, round, terminal, or source metadata reaches bc_update.
                sample = {'features': row['features'], 'action': row['action']}
                samples.append(sample)
                order.update(json_bytes(sample))
            else:
                episode_identity = {k: ep[k] for k in ('seed', 'difficulty', 'mode', 'budget')}
                if kind == 'full-terminal-v1':
                    require(set(row) <= {'type', 'seed', 'difficulty', 'terminal', 'rejectedLabels', 'split'},
                            'Unexpected full-terminal episode field')
                    terminal = row['terminal']
                    full_terminal(terminal, episode_identity, schema)
                else:
                    require(set(row) <= {'type', 'seed', 'difficulty', 'mode', 'budget', 'victory',
                                        'rejectedLabels', 'split', *COUNTERS}, 'Unexpected legacy episode field')
                    require(all(row.get(k) == v for k, v in episode_identity.items()), 'Legacy episode identity')
                    terminal = row
                    for k in COUNTERS:
                        require(type(row.get(k)) is int and row[k] >= 0, 'Invalid legacy counter: ' + k)
                    require(all(row[k] == 0 for k in ERROR_KEYS), 'Legacy terminal errors')
                require(terminal.get('victory') is ep['victory'], 'Episode outcome differs from manifest')
                rejected = row.get('rejectedLabels')
                require(type(rejected) is int and rejected >= 0, 'Rejected-label count')
                require(counts[key] == ep['samples'], 'Missing episode samples')
                require(counts[key] <= terminal['steps'] <= counts[key] + rejected, 'Teacher step/sample accounting')
                # roundsPassed excludes the round of elimination in the original engine.
                require(rounds.get(key, 0) <= terminal['rounds'] + 1, 'Sample after terminal round')
                wins[key[0]] += int(terminal['victory'])
                closed.add(key)
    require(header is not None and closed == set(expected) and seen == set(expected), 'Missing/truncated episode tails')
    require(len(samples) == manifest['sampleCount'] and samples, 'Dataset sample count')
    require(digest.hexdigest() == manifest.get('datasetSHA256'), 'Dataset SHA256 mismatch')
    require(wins['ABYSS'] == 0, 'Unexpected ABYSS win: original dataset has no ABYSS wins')
    return samples, {'schema': schema, 'rules': RULES, 'datasetSHA256': digest.hexdigest(),
                     'manifestSHA256': sha(manifest_path), 'manifest': manifest,
                     'sampleOrderSHA256': order.hexdigest(), 'sampleCount': len(samples),
                     'episodeCount': len(closed), 'winsByDifficulty': dict(wins),
                     'terminalTrust': ('inherited from source collector metadata; no raw terminals independently verified'
                                       if kind == 'legacy-demo' else 'full terminals structurally validated; collector provenance still trusted')}
