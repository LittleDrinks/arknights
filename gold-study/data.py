"""Admit exactly six frozen personal wins and twelve verified full recaptures."""
import gzip
import io
from pathlib import Path

from contracts import A, C, R, MIB, REVIEW, bounded, pool_sha, project, recheck


def artifact(path, row, gold=False):
    packed = bounded(path, 64 * MIB)
    C.require(C.digest(packed) == row['artifactSHA256'] and len(packed) == row['bytes'], 'Artifact SHA/bytes')
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
        raw = stream.read(64 * MIB + 1)
        C.require(len(raw) <= 64 * MIB and not stream.read(1), 'Raw decompression/EOF limit')
    C.require(raw.endswith(b'\n') and len(raw.splitlines()) == 1, 'Exactly one full raw response required')
    if gold:
        C.require(C.digest(raw) == row['rawSHA256'] and len(raw) == row['rawBytes'], 'Raw SHA/bytes')
    return C.parse(raw), packed, C.digest(raw), len(raw)


def admit(directory, expected, code, collector, game, game_pin, evidence):
    files, sets = {}, {}

    def remember(path, raw):
        path = Path(path).resolve()
        files[str(path)] = C.digest(raw)
        return raw

    raw = remember(directory / 'manifest.json', bounded(directory / 'manifest.json', 8 * MIB))
    C.require(C.digest(raw) == expected, 'Recapture manifest SHA mismatch')
    m = C.parse(raw)
    C.require(m['status'] == 'complete' and m.get('finishedAt') and len(m['results']) == 12,
              'Only complete twelve-result recapture is consumable')
    C.require(m['gameIdentity'] == game_pin and m['fixedConfig'] == dict(
        split='train', poolSeed=313, teams=R.TEAMS, difficulty='ABYSS', budget=128), 'Recapture game/config')
    implementation = {p.name: C.sha(p) for p in (code / 'gold-features').iterdir() if p.suffix in ('.mjs', '.py')}
    C.require(m['implementation'] == implementation, 'Recapture implementation identity')
    original = Path(m['manifest']['path'])
    C.require(original.is_absolute(), 'Absolute original manifest required')
    frozen, frozen_raw = A.frozen(original, m['manifest']['sha256'])
    remember(original, frozen_raw)
    copied = remember(directory / 'source-manifest.json', bounded(directory / 'source-manifest.json', 8 * MIB))
    C.require(copied == frozen_raw, 'Source manifest copy differs')
    bindings = R.source_bindings(frozen, code, collector)
    C.require(m['sources'] == bindings, 'Recapture source bindings')
    for binding in bindings:
        files[binding['local']] = binding['sha256']
    selection_path = Path(m['selection']['path'])
    selected_raw = remember(directory / 'selection.json', bounded(directory / 'selection.json', MIB))
    C.require(selection_path.is_absolute() and C.digest(selected_raw) == m['selection']['sha256'] and
              remember(selection_path, bounded(selection_path, MIB)) == selected_raw, 'Selection copy/SHA')
    selected = C.parse(selected_raw)
    C.require(type(selected) is list and len(selected) == 6 and all(type(p) is str for p in selected), 'Six selected games required')
    paths = [(original.parent / p).resolve() for p in selected]
    C.require(len(set(paths)) == 6, 'Duplicate selected game')
    R.raw_file_set(original.parent, frozen)
    sets[str(original.parent)] = sorted(p.name for p in original.parent.glob('*.json.gz'))
    names = [r['artifact'] for r in m['results']]
    C.require(len(set(names)) == 12 and all(Path(n).name == n for n in names), 'Recapture artifact paths')
    sets[str(directory)] = sorted(names)
    C.require(sorted(p.name for p in directory.glob('*.json.gz')) == sorted(names), 'Recapture raw file set')
    evidence.put('recapture-manifest.json', raw)
    evidence.put('source-manifest.json', copied)
    evidence.put('source-selection.json', selected_raw)
    samples, labels, games, used, total_raw = {'off': [], 'on': []}, [], [], set(), 0
    schema = {**game['schema'], 'version': R.VERSION}
    manifest_pool = pool_sha(code)
    for index, path in enumerate(paths):
        rows = [r for r in frozen['games'] if 'artifact' in r and (original.parent / r['artifact']).resolve() == path]
        C.require(len(rows) == 1, 'Selected source row must be unique')
        row = rows[0]
        old, packed, raw_sha, size = artifact(path, row)
        total_raw += size
        remember(path, packed)
        R.positive(old, row)
        C.require(A.complete_contract(old, row, game, manifest_pool), 'Natural personal success required')
        old_copy = f'old-{index}.json.gz'
        evidence.put(old_copy, packed)
        games.append(dict(row=row, episodeGroup=row['seed'], source=str(path), copy=old_copy,
                          rawSHA256=raw_sha, rawBytes=size))
        for mode in ('off', 'on'):
            matches = [(j, r) for j, r in enumerate(m['results']) if
                       (r['mode'], r['seed'], r['pool']) == (mode, row['seed'], row['pool'])]
            C.require(len(matches) == 1, 'Exactly one OFF/ON record per game')
            j, result = matches[0]
            used.add(j)
            C.require(result['status'] == 'verified' and result['source'] == str(path) and
                      result['sourceSHA256'] == row['artifactSHA256'], 'Verified old source binding')
            new_path = directory / result['artifact']
            new, new_packed, _, new_size = artifact(new_path, result, gold=True)
            total_raw += new_size
            C.require(total_raw <= 512 * MIB, 'Aggregate expanded input budget')
            remember(new_path, new_packed)
            R.verify_recapture(old, new, mode)  # Entire response, including arbitrary metadata.
            C.require(new['schema'] == schema, 'Recapture/static vocabulary mismatch')
            project(new['terminal'], schema, mode)
            for sample in new['samples']:
                project(dict(schema=new['schema'], features=sample['features'], done=False), schema, mode)
                samples[mode].append({k: sample[k] for k in ('features', 'action')})
            evidence.put(f'new-{index}-{mode}.json.gz', new_packed)
        for i, sample in enumerate(old['samples']):
            labels.append(dict(index=len(labels), labelIndex=i, source=str(path), seed=row['seed'],
                               pool=row['pool'], episodeGroup=row['seed'], round=sample['round']))
    groups = sorted({r['episodeGroup'] for r in labels})
    C.require(len(used) == 12 and len(labels) == 498 and len(groups) == 6 and
              len(samples['off']) == len(samples['on']) == 498, 'Six episode groups / 498 paired labels required')
    metadata = dict(inputReviewStatus=REVIEW, recapture=m, games=games, labels=labels,
                    episodeGroups=groups, positiveGames=6, positiveLabels=498, inputFiles=files,
                    rawFileSets=sets, schema=schema, inputSHA256=C.digest(C.encoded(files)))
    recheck(metadata)
    evidence.json('input-metadata.json', metadata)
    return samples, metadata
