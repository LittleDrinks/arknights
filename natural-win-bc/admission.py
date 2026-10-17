"""Whole frozen batch audit. Only positive feature/action pairs survive in RAM."""
from collections import Counter
import gzip
from pathlib import Path

from support import C, MIB, REVIEW, SELECTOR, bounded, capture_sources

PLAN = [(seed, pool) for seed in range(30001, 31025) for pool in ('normal', 'mixed')]


def frozen(path, expected):
    raw = bounded(path, 8 * MIB)
    C.require(C.digest(raw) == expected, 'Frozen manifest SHA mismatch')
    m = C.parse(raw)
    C.require(m['status'] in ('complete', 'incomplete', 'failed') and m.get('finishedAt'), 'Batch not frozen/running')
    C.require(m['planned'] == 2048 and m['pairedTrainingSeeds'] == 1024 and len(m['games']) == 2048,
              'Full 2048 planned denominator required')
    C.require([(r['seed'], r['pool']) for r in m['games']] == PLAN and
              all(type(r['seed']) is int and r['status'] in ('complete', 'failed', 'not-run') for r in m['games']),
              'Fixed paired training seed plan/status')
    C.require(m['status'] != 'complete' or all(r['status'] == 'complete' for r in m['games']), 'False complete batch')
    capture_sources(m)
    return m, raw


def read_artifact(path, row):
    raw = bounded(path, 64 * MIB)
    C.require(len(raw) == row['bytes'] and C.digest(raw) == row['artifactSHA256'], 'Artifact SHA/bytes')
    # Read through every gzip trailer/member, including CRC and actual EOF.
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
        payload = f.read(64 * MIB + 1)
        C.require(len(payload) <= 64 * MIB and not f.read(1), 'Expanded artifact limit')
    C.require(payload.endswith(b'\n'), 'Full JSON EOF/newline')
    return C.parse(payload), len(payload), {'sha256': C.digest(raw), 'bytes': len(raw),
                                           'payloadSHA256': C.digest(payload), 'payloadBytes': len(payload)}


def complete_contract(data, row, game, pool_manifest):
    C.require(set(data) == {'ok', 'schema', 'rules', 'terminal', 'samples', 'teacher', 'rejectedLabels'}, 'Full demo keys')
    C.require(data['ok'] is True and data['schema'] == game['schema'] and data['rules'] == C.RULES and
              data['teacher'] == 'upstream-bot/rehearsal=0', 'Demo schema/rules/teacher')
    samples, t = data['samples'], data['terminal']
    C.require(type(samples) is list and len(samples) <= 4096 and type(data['rejectedLabels']) is int and
              0 <= data['rejectedLabels'] <= 4096, 'Label/rejection bounds')
    C.require(type(t['steps']) is int and len(samples) <= t['steps'] <= len(samples) + data['rejectedLabels'],
              'Teacher accepted/missing/rejected step accounting')
    primary = C.terminal_contract(t, seed=row['seed'], steps=t['steps'], schema=game['schema'])
    C.require(1 <= t['rounds'] <= 14, 'ABYSS terminal rounds')
    # Admission is stricter than model eval: every teacher error counter must be zero.
    for key in ('engineErrors', 'actionErrors', 'controllerErrors', 'controllerDiagnosticErrors', 'partnerActionErrors'):
        C.require(type(t[key]) is int and t[key] == 0, 'Teacher errors: ' + key)
    C.require(type(t['summary']['errors']) is int and t['summary']['errors'] == 0, 'Summary errors')
    partners = t['episode']['partners']
    team = C.TEAMS['original' if row['pool'] == 'normal' else 'mixed']
    families = {'original-v1': 'upstream-bot', 'buy2-v1': 'first-offer-buy2', 'saver-v1': 'level-first-saver'}
    C.require(partners == dict(pool=row['pool'], split='train', poolSeed=313, fixed=team,
              manifest='coop-heuristics-v1', manifestSHA256=pool_manifest,
              seats=[dict(seat=i + 1, id=x, family=families[x]) for i, x in enumerate(team)]), 'Teacher pool/seat identity')
    for p in t['summary']['players']:
        C.require(p['victory'] is (t['victory'] and p['alive']) and
                  (not p['alive'] or p['eliminatedRound'] is None), 'Individual vs team outcome consistency')
    if t['victory']:
        C.require(t['rounds'] == 14 and t['summary']['reason'] == 'victory', 'Incomplete team victory')
        C.require(any(p['victory'] for p in t['summary']['players']) and
                  all(not p['alive'] or p['roundsPassed'] == 14 for p in t['summary']['players']),
                  'Winning team survivor outcomes')
    dims, previous = C.dimensions(game['schema']), 0
    for sample in samples:
        C.require(set(sample) == {'features', 'action', 'round'} and type(sample['round']) is int and
                  previous <= sample['round'] <= 14 and sample['round'] >= 1, 'Label round metadata/order')
        previous = sample['round']
        C.sample_contract({k: sample[k] for k in ('features', 'action')}, dims)
    positive = (t['victory'] and primary['victory'] and primary['alive'] and
                primary['roundsPassed'] == 14 and data['rejectedLabels'] == 0)
    fields = dict(teamVictory=t['victory'], learnerVictory=primary['victory'], learnerAlive=primary['alive'],
                  rounds=t['rounds'], learnerRounds=primary['roundsPassed'], rejectedLabels=data['rejectedLabels'],
                  samples=len(samples), eligiblePositive=positive)
    for k, value in fields.items():
        C.require(type(row[k]) is type(value) and row[k] == value, 'Manifest/raw mismatch: ' + k)
    C.require(not positive or (samples and t['steps'] == len(samples)), 'Empty/incomplete positive labels')
    return positive


def admit(directory, expected, game, pool_manifest, evidence):
    m, raw = frozen(directory / 'manifest.json', expected)
    evidence.put('frozen-manifest.json', raw)
    samples, labels, games, identities = [], [], [], {}
    positive_bytes, expanded, compressed = 0, 0, 0
    for row in m['games']:
        name = f"{row['pool']}-{row['seed']}.json.gz"
        record = {'row': row, 'episodeGroup': row['seed'], 'selectedLabels': 0}
        if row['status'] != 'not-run':
            C.require(row.get('startedAt') and row.get('finishedAt'), 'Missing row timestamps')
        if row['status'] == 'failed':
            C.require(row.get('error') and not row.get('eligiblePositive', False), 'Failed row error/positive claim')
        if row['status'] == 'not-run':
            C.require(not any(k in row for k in ('artifact', 'artifactSHA256', 'eligiblePositive')), 'Unrun row has results')
        if row['status'] == 'complete' or 'artifact' in row:
            C.require(row['artifact'] == name and Path(name).name == name, 'Artifact identity/path')
            data, size, identity = read_artifact(directory / name, row)
            identities[name] = identity
            expanded += size
            compressed += identity['bytes']
            C.require(expanded <= 16 * 1024 * MIB and compressed <= 2 * 1024 * MIB, 'Aggregate input limit')
            # Keep full terminal, teacher and all other raw metadata, even for failed/negative rows.
            audit = {k: v for k, v in data.items() if k != 'samples'}
            record['evidence'] = evidence.json('audit-' + name, audit, compress=True)
            record['auditFile'] = 'audit-' + name
            record['input'] = identity
            if row['status'] == 'complete':
                positive = complete_contract(data, row, game, pool_manifest)
                if positive:
                    positive_bytes += size
                    C.require(positive_bytes <= 128 * MIB and len(samples) + len(data['samples']) <= 200000,
                              'Positive data memory/label budget')
                    record['selectedLabels'] = len(data['samples'])
                    for i, s in enumerate(data['samples']):
                        labels.append(dict(index=len(samples), artifact=name, labelIndex=i, seed=row['seed'],
                                           pool=row['pool'], episodeGroup=row['seed'], round=s['round']))
                        samples.append({k: s[k] for k in ('features', 'action')})
        games.append(record)
    C.require(samples, 'No admissible positive labels')
    identities['manifest.json'] = {'sha256': expected, 'bytes': len(raw)}
    groups = sorted({l['episodeGroup'] for l in labels})
    metadata = dict(inputReviewStatus=REVIEW, selector=SELECTOR, games=games, labels=labels,
                    plannedGames=2048, plannedEpisodeGroups=1024, statusCounts=dict(Counter(r['status'] for r in m['games'])),
                    positiveGames=sum(bool(r['selectedLabels']) for r in games), positiveLabels=len(samples),
                    eligibleEpisodeGroups=groups, eligibleGroupCount=len(groups), inputFiles=identities,
                    inputSHA256=C.digest(C.encoded(identities)), manifestSHA256=expected)
    recheck(directory, identities)
    return samples, metadata


def recheck(directory, identities):
    for name, identity in identities.items():
        p = directory / name
        C.require(p.stat().st_size == identity['bytes'] and C.sha(p) == identity['sha256'], 'Frozen input drift: ' + name)
