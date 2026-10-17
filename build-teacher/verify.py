#!/usr/bin/env python3
"""Read-only verification of committed evidence. Does not start any simulation."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
manifest = json.loads((HERE / 'source-hashes.json').read_text())
for rel, sha in manifest.get('implementation', {}).items():
    assert hashlib.sha256((HERE / rel).read_bytes()).hexdigest() == sha, rel
for rel, sha in manifest.get('evidence', {}).items():
    assert hashlib.sha256((HERE.parent / rel).read_bytes()).hexdigest() == sha, rel
summary = json.loads((HERE / 'results/summary.json').read_text())
budget = json.loads((HERE / 'results/budget.json').read_text())
assert len(summary['cases']) == 8
assert budget['childWallSeconds'] <= 40
assert budget['peakSampledAggregateRssBytes'] <= 768 * 1024**2
assert sum(p.stat().st_size for p in HERE.rglob('*') if p.is_file()) < 20 * 1024**2
assert all(all(c.values()) for c in summary['realControls'].values())
for case in summary['cases']:
    assert case['completed'] and case['status'] == 'complete' and case['errors'] == 0, case['label']
    assert case['nativeTempDiscards'] == 0
    rows = [json.loads(s) for s in (HERE / 'results' / (case['label'] + '.jsonl')).read_text().splitlines()]
    for row in rows:
        if row['type'] == 'action' and row['action'] == 'buy' and row['result'].get('ok'):
            assert row['visible'] and not row['visible']['sold']
        if row['type'] == 'action' and row['action'] == 'sell' and row['result'].get('ok'):
            assert row['after'] >= row['before'], row
        if row['type'] == 'prep' and row['round'] >= 4:
            assert row['state']['units'] == 8
print('PASS: eight completed natural records; off controls equal; visible successful buys; legal sale refunds; budgets; hashes')
