"""Admit the complete winning-labels/v1 artifact, then select by seat/round only."""
from collections import Counter, defaultdict
import gzip
import json
import random

from common import CONFIG, RULES, digest, dimensions, encoded, parse, require, sample_contract, sha, terminal_contract

FILES = {'samples.jsonl.gz', 'labels.jsonl.gz', 'dispositions.jsonl.gz', 'metadata.json.gz'}


def read_gzip(path, limit=16 * 1024 * 1024):
    require(path.stat().st_size <= 5_000_000, 'Compressed input budget')
    with gzip.open(path, 'rb') as f:
        payload = f.read(limit + 1)
        require(len(payload) <= limit and not f.read(1), 'Expanded input budget')
    # Reading to EOF checks all members, trailer CRC, truncated/garbage tails.
    require(payload.endswith(b'\n'), 'Incomplete input EOF')
    lines = payload[:-1].split(b'\n')
    require(all(lines), 'Empty record')
    return [parse(line) for line in lines], lines, {
        'bytes': path.stat().st_size, 'sha256': sha(path),
        'payloadBytes': len(payload), 'payloadSHA256': digest(payload)}


def select68(labels):
    # Hamilton proportional allocation. Deterministic numeric seat/round tie break.
    groups = defaultdict(list)
    for i, label in enumerate(labels):
        groups[(label['seat'], label['round'])].append(i)
    keys = sorted(groups)
    counts = {k: 68 * len(groups[k]) // 207 for k in keys}
    remainder = 68 - sum(counts.values())
    for k in sorted(keys, key=lambda k: (-(68 * len(groups[k]) % 207), k))[:remainder]:
        counts[k] += 1
    rng = random.Random(CONFIG['selectionSeed'])
    selected, strata = [], []
    for k in keys:
        indices = list(groups[k])
        rng.shuffle(indices)
        chosen = sorted(indices[:counts[k]])
        selected.extend(chosen)
        strata.append({'seat': k[0], 'round': k[1], 'available': len(indices), 'selected': chosen})
    require(len(selected) == len(set(selected)) == 68, 'Selection cardinality')
    return sorted(selected), strata


def original_ids(value):
    ids = {'ai_0': 'learner', 'ai_1': 'partner-1', 'ai_2': 'partner-2', 'ai_3': 'partner-3'}
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [original_ids(x) for x in value]
    if isinstance(value, dict):
        return {original_ids(k): original_ids(v) for k, v in value.items()}
    return value


def admit(input_dir, primary, *, source, game):
    audit_path = input_dir / 'audit.json'
    require(audit_path.stat().st_size < 1_000_000, 'Audit size')
    audit = parse(audit_path.read_bytes())
    require(audit['ok'] is True and audit['primaryExact'] == 68 and audit['samples'] == 207 and
            audit['independentEpisodes'] == 1 and set(audit['files']) == FILES, 'Incomplete audit')
    rows, lines, identities = {}, {}, {'audit.json': {'sha256': sha(audit_path)}}
    for name in sorted(FILES):
        p = input_dir / name
        require(p.stat().st_size == audit['files'][name]['bytes'] and sha(p) == audit['files'][name]['sha256'],
                'Audit output SHA/size: ' + name)
        rows[name], lines[name], identities[name] = read_gzip(p)
    require(len(rows['metadata.json.gz']) == 1, 'Metadata EOF')
    m = rows['metadata.json.gz'][0]
    samples, labels, dispositions = (rows[n] for n in ('samples.jsonl.gz', 'labels.jsonl.gz', 'dispositions.jsonl.gz'))
    primary_samples, _, identities['primary'] = read_gzip(primary)
    require(len(samples) == len(labels) == 207 and len(primary_samples) == 68, '207/68 full EOF sample counts')
    require(m['format'] == 'winning-labels/v1' and m['rules'] == RULES and m['schema'] == game['schema'] and
            m['staticData']['rules'] == RULES and m['staticData']['dataSHA256'] == game['dataSHA256'], 'Schema/rules/static data mismatch')
    require(m['mode'] == 'coop' and m['difficulty'] == 'ABYSS' and m['samples'] == 207 and
            m['independentEpisodes'] == 1 and m['positiveSeatSequences'] == 2, 'Source scope')
    require(m['episodeGroup'] == m['traceSHA256'] == m['provenance']['trace']['sha256'] and
            len(m['episodeGroup']) == 64, 'Single episodeGroup')
    require(m['sources'] and all(source['originalFiles'].get(k) == v for k, v in m['sources'].items()), 'Capture source drift')
    require(m['implementationSources'] and m['limitations'], 'Missing extraction provenance/limitations')
    require(audit['rawSHA256'] == m['raw']['sha256'] and audit['referenceSHA256'] == m['reference']['sha256'], 'Audit source binding')
    for key in ('sha256', 'bytes', 'payloadBytes', 'payloadSHA256'):
        require(identities['primary'][key] == m['reference'][key], 'Primary reference ' + key)
    dims = dimensions(m['schema'])
    for sample in samples + primary_samples:
        sample_contract(sample, dims)
    t, header = m['terminal'], m['header']
    terminal_contract(t, seed=50003, steps=68, schema=m['schema'])
    require(t['victory'] is True and t['rounds'] == 14 and t['summary']['reason'] == 'victory', 'Source full victory')
    require(header['type'] == 'episode' and header['schema'] == 'arknights.bot-transitions.v1' and
            header['config'] == 'coop:ABYSS' and header['seed'] == 50003 and header['players'] == 4 and
            header['commit'] == RULES and header['rehearsal'] == 0 and header['policySteps'] == 68 and
            header['engineErrors'] == header['schedulerErrors'] == 0, 'Capture header')
    require(original_ids(header['summary']) == t['summary'] and header['provenance'] == m['provenance'] and
            m['provenance']['episode'] == t['episode'] and m['provenance']['rules'] == RULES,
            'Complete header/terminal provenance mismatch')
    require(header['victory'] is True and header['roundsPassed'] == 14 and header['terminalReason'] == 'victory', 'Header outcome')
    require(m['seats'] == audit['seats'], 'Seat audit mismatch')
    expected = {'learner': 0, 'partner-1': 93, 'partner-2': 114, 'partner-3': 0}
    for p in t['summary']['players']:
        seat = m['seats'][p['playerId']]
        positive = p['seat'] in (1, 2)
        require(seat == dict(seat=p['seat'], individualVictory=positive, teamVictory=True, alive=positive,
                            roundsPassed=14 if positive else 10, eliminatedRound=None if positive else 11,
                            positive=positive, samples=expected[p['playerId']]), 'Positive seat contract')
        require(p['victory'] is positive and p['alive'] is positive and p['roundsPassed'] == seat['roundsPassed'] and
                p['eliminatedRound'] == seat['eliminatedRound'], 'Terminal positive identity')
    require(len(dispositions) == audit['transitions'] == header['methodCalls'] == 1066 and
            [d['seq'] for d in dispositions] == list(range(1066)), 'Full disposition EOF/sequence')
    require(dict(Counter(d['selection'] for d in dispositions)) == audit['dispositionCounts'], 'Disposition histogram')
    require(sum(not d['accepted'] for d in dispositions) == audit['rejected'] == 0, 'Rejected source action')
    for d in dispositions:
        require(d['playerId'] in expected and d['accepted'] is d['result']['ok'] and
                (d['parentSeq'] is None or type(d['parentSeq']) is int and 0 <= d['parentSeq'] < d['seq']), 'Disposition identity')
    validation = m['primaryValidation']
    require(validation['exact'] is True and validation['samples'] == 68 and len(validation['primary']) == 68 and
            [p['traceIndex'] for p in validation['primary']] == list(range(68)), 'Primary exact validation')
    primary_seqs = [p['seq'] for p in validation['primary']]
    require(primary_seqs == sorted(set(primary_seqs)) and all(0 <= s < 1066 for s in primary_seqs), 'Primary trace indices')
    require(all(dispositions[s]['playerId'] == 'learner' and dispositions[s]['source'] == 'policy' and
                dispositions[s]['accepted'] is True for s in primary_seqs), 'Primary trace binding')
    positive_seqs = [d['seq'] for d in dispositions if d['selection'] == 'positive-label']
    require([l['seq'] for l in labels] == positive_seqs, 'All positive labels/dispositions must bind in order')
    counts = Counter()
    for i, (label, sample) in enumerate(zip(labels, samples)):
        seat, round_ = label['seat'], label['round']
        require(type(seat) is int and seat in (1, 2) and label['playerId'] == f'partner-{seat}' and
                type(round_) is int and 1 <= round_ <= 14 and label['sample'] == i and
                label['source'] == 'partner-controller' and label['counterMode'] == 'observable-teammate-teacher' and
                label['prepBudget'] == 128 and type(label['prepSteps']) is int and label['prepSteps'] >= 0,
                'Label identity/counter contract')
        d = dispositions[label['seq']]
        require(all(label[k] == d[k] for k in ('playerId', 'source', 'method', 'parentSeq')) and
                label['rawAction'] == d['action'] and d['accepted'] is True, 'Label raw binding')
        require(label['sampleSHA256'] == digest(lines['samples.jsonl.gz'][i]), 'Sample SHA')
        cs, a = label['candidates'], sample['action']
        require(len(cs) == len(sample['features']['candidates']) and label['candidateIndex'] == a and
                label['candidate'] == cs[a], 'Candidate binding/order')
        # Candidate objects contain JSON integer/string/bool fields, no floating features.
        require(label['candidateSHA256'] == digest(json.dumps(cs, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()),
                'Candidates SHA')
        require(all(c['type'] in ('buy', 'sell', 'refresh', 'levelUp', 'ready') for c in cs), 'Action space')
        require(sample['features']['global'][0] == round_ / 20 and
                sample['features']['global'][12] == label['prepSteps'] / 128, 'Pre-action round/teacher steps')
        counts[label['playerId']] += 1
    require(dict(counts) == {'partner-1': 93, 'partner-2': 114}, 'Positive 93/114')
    selected, strata = select68(labels)
    selections = {'primary68': list(range(68)), 'survivor68': selected, 'survivor207': list(range(207))}
    return {'samples': {'primary68': primary_samples, 'survivor68': samples, 'survivor207': samples},
            'selections': selections, 'strata': strata, 'metadata': m, 'audit': audit, 'labels': labels,
            'inputSHA': identities, 'rawInputSHA256': digest(encoded(identities)),
            'rawCaptureHashStatus': 'Declared by extractor; original raw capture not re-read by this command.',
            'inputReviewStatus': 'interface-checked-independent-review-record-external'}
