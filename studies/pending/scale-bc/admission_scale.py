"""Whole-corpus admission, preserving the original old-source admission contract."""
from collections import Counter
from pathlib import Path
import time

from core import (A, C, N, R, S, CODE, MIB, OLD_SHA, PARENT_SHA, REFERENCE_SHA,
                  REFERENCE_REPORT_SHA, PrefixEvidence, file_id, inventory, read, require, safe)


def exact(a, b, message):
    require(C.encoded(a) == C.encoded(b), message)  # Do not equate bool with int.


def bound_json(path, sha, limit=64*MIB):
    raw = read(path, limit)
    require(type(sha) is str and len(sha) == 64 and C.digest(raw) == sha, 'Bound JSON SHA: ' + str(path))
    return C.parse(raw), raw


def header(m):
    require(m['kind'] == 'natural-teacher-scale/v1' and m['status'] in ('complete', 'incomplete') and
            m.get('finishedAt') and m.get('frozen') is True and m.get('globalFailed') is False and
            m.get('globalErrors') == [] and m.get('trainingAllowed') is False,
            'Source must be frozen complete/incomplete without global failure')
    require(type(m['planned']) is int and m['planned'] == 8192 and
            type(m['pairedTrainingSeeds']) is int and m['pairedTrainingSeeds'] == 4096 and
            type(m['games']) is list and len(m['games']) == 8192, 'Exact full 8192 plan')
    planned = S.plan()
    for expected, row in zip(planned, m['games']):
        exact({k: row[k] for k in ('index', 'seed', 'pool', 'lane')},
              {k: expected[k] for k in ('index', 'seed', 'pool', 'lane')}, 'Exact training plan identity')
        require(row['status'] in ('complete', 'not-run'), 'Failed/interrupted/unknown source case')
    require((m['status'] == 'complete') == all(r['status'] == 'complete' for r in m['games']),
            'Complete/incomplete must match full plan statuses')


def receipt(path, sha, manifest_sha, source, game):
    value, raw = bound_json(path, sha, MIB)
    require(value.get('kind') == 'scale-bc-source-acceptance/v1' and value.get('accepted') is True,
            'Main independent source acceptance receipt required')
    for key in ('acceptedBy', 'acceptedAt', 'evidence'):
        require(type(value.get(key)) is str and value[key].strip(), 'Receipt provenance: ' + key)
    exact(value.get('bindings'), dict(newManifestSHA256=manifest_sha, oldManifestSHA256=OLD_SHA,
          newSourceSHA256=C.digest(C.encoded(source)), gameSHA256=C.digest(C.encoded(game)),
          referenceReportSHA256=REFERENCE_REPORT_SHA, parentSHA256=PARENT_SHA), 'Receipt bindings')
    return value, raw


def new_admit(directory, sha, game, source, deadline):
    """No eligible-journal shortcut: reconcile all raw and lane evidence before selecting."""
    directory = safe(directory)
    m, raw = bound_json(directory / 'manifest.json', sha)
    header(m)
    before = inventory(directory, deadline)
    expected_source = dict(source=source, game=game, poolSHA256=N.pool_sha(CODE))
    for name in ('source-before.json', 'source-after.json'):
        exact(C.parse(read(directory / name, 2*MIB)), expected_source, 'Captured source/game/pool identity')
    audit = R.reconcile(directory, S.plan(), deadline)
    require(not audit['errors'], 'Independent reconciliation failed: ' + repr(audit['errors'][:10]))
    for key in ('games', 'lanes', 'files', 'filesSHA256'):
        exact(m[key], audit[key], 'Manifest/reconciliation mismatch: ' + key)
    exact(audit['files'], {k: v for k, v in before['files'].items() if k != 'manifest.json'},
          'Full inventory/reconciliation mismatch')
    samples, labels, positives = [], [], []
    # Reconciliation already parsed every complete raw, including all negatives. Re-read
    # positive payloads only for extraction, with the same SHA and semantic checks.
    for row in audit['games']:
        if row['status'] != 'complete' or row['eligiblePositive'] is not True:
            continue
        path = safe(directory / row['artifact'])
        payload_id, payload = R.payload_identity(path, S.REQUEST_BYTES, deadline, retain=True)
        exact({**file_id(path, deadline), **payload_id}, row['artifactIdentity'], 'Positive raw drift')
        data = C.parse(payload)
        require(A.complete_contract(data, row, game, expected_source['poolSHA256']), 'Strict positive contract')
        require(len(samples) + len(data['samples']) <= 6800, 'More than 6800 new positives; no prefix truncation')
        positives.append(dict(index=row['index'], seed=row['seed'], pool=row['pool'], labels=len(data['samples'])))
        for index, sample in enumerate(data['samples']):
            # round is audit metadata; only features/action reach the original learner.
            samples.append({k: sample[k] for k in ('features', 'action')})
            labels.append(dict(index=len(labels), corpus='new8192', artifact=row['artifact'],
                               labelIndex=index, seed=row['seed'], pool=row['pool'],
                               episodeGroup=row['seed'], round=sample['round']))
    require(samples, 'No new eligible positive labels')
    return samples, dict(manifestSHA256=sha, fullInventory=before, labels=labels, positiveGames=positives,
                         statusCounts=dict(Counter(r['status'] for r in audit['games'])),
                         plannedGames=8192, plannedEpisodeGroups=4096, fullRawSemanticAudit=True), raw


def reference(directory, deadline):
    """Read fixed historical evidence; admission-only never imports torch."""
    directory = safe(directory)
    report, report_raw = bound_json(directory / 'train.json', REFERENCE_REPORT_SHA)
    require(report['status'] == 'complete' and report['parent']['sha256'] == PARENT_SHA and
            report['models']['steps3000']['checkpointSHA256'] == REFERENCE_SHA, 'Historical fit report')
    metadata, _ = bound_json(directory / 'input-metadata.json', report['metadataSHA256'])
    draw_plan, _ = bound_json(directory / 'draw-plan.json', report['drawPlanSHA256'])
    require(file_id(directory / 'steps3000.pt', deadline, 64*MIB)['sha256'] == REFERENCE_SHA,
            'Historical steps3000 checkpoint SHA')
    require(draw_plan['selectedIndices'] == list(range(498)) and
            draw_plan['expandedIndices'] == [i % 498 for i in range(6800)] and
            draw_plan['orderSeed'] == 101 and draw_plan['epochs']['steps3000'] == 15, 'Historical selection/budget')
    return metadata, draw_plan, report, inventory(directory, deadline, 256*MIB)


def reorder_old(samples, metadata, reference_metadata):
    def key(label):
        return (Path(label.get('artifact', label.get('source', ''))).name, label['labelIndex'],
                label['seed'], label['pool'], label['round'])
    actual = [key(label) for label in metadata['labels']]
    wanted = [key(label) for label in reference_metadata['labels']]
    require(len(actual) == len(wanted) == len(set(actual)) == len(set(wanted)) == 498 and
            set(actual) == set(wanted) and metadata['positiveGames'] == 6 and metadata['eligibleGroupCount'] == 6,
            'Exact original six games / 498 labels and historical order membership')
    mapping = {value: i for i, value in enumerate(actual)}
    order = [mapping[value] for value in wanted]
    labels = [{**metadata['labels'][i], 'index': j, 'corpus': 'old6'} for j, i in enumerate(order)]
    return [samples[i] for i in order], {**metadata, 'labels': labels, 'originalAdmissionIndices': order,
           'historicalSampleOrderSHA256': C.digest(C.encoded(wanted))}


def admit_all(old_dir, new_dir, new_sha, ref_dir, game, collector_source, evidence, deadline, names):
    ref_meta, ref_plan, ref_report, ref_inventory = reference(ref_dir, deadline)
    old_inventory = inventory(old_dir, deadline)
    # This is the original exact old-source admission, including its failed/not-run rules.
    old_samples, old_meta = A.admit(old_dir, OLD_SHA, game, N.pool_sha(CODE), PrefixEvidence(evidence, 'old-'))
    for row in old_meta['games']:
        if 'auditFile' in row:
            row['auditFile'] = 'old-' + row['auditFile']
    old_samples, old_meta = reorder_old(old_samples, old_meta, ref_meta)
    new_samples, new_meta, raw = new_admit(new_dir, new_sha, game, collector_source, deadline)
    require('combined' not in names or len(old_samples) + len(new_samples) <= 6800,
            'Combined positives exceed 6800; all labels must be retained')
    evidence.put('new-frozen-manifest.json', raw)
    evidence.json('old-metadata.json.gz', old_meta, compress=True)
    evidence.json('new-metadata.json.gz', new_meta, compress=True)
    evidence.json('historical-draw-plan.json.gz', ref_plan, compress=True)
    result = dict(old=old_meta, new=new_meta, referenceReport=ref_report,
                  referenceInventory=ref_inventory, oldInventory=old_inventory)
    recheck(old_dir, new_dir, ref_dir, result, deadline)
    return dict(old6Reference=old_samples, newOnly=new_samples, combined=old_samples+new_samples), result


def recheck(old_dir, new_dir, ref_dir, metadata, deadline):
    for path, expected in ((old_dir, metadata['oldInventory']), (new_dir, metadata['new']['fullInventory']),
                           (ref_dir, metadata['referenceInventory'])):
        exact(inventory(path, deadline), expected, 'Whole input inventory drift: ' + str(path))


def arm_labels(metadata):
    old, new = metadata['old']['labels'], metadata['new']['labels']
    return dict(old6Reference=old, newOnly=new,
                combined=[{**row, 'index': i} for i, row in enumerate(old+new)])
