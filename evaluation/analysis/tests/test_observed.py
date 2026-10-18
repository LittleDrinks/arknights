"""Read-only independent reconciliation against the real 96 records when available."""
import hashlib
import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / 'experiments/planner-ablation-2026-10-18'


@unittest.skipUnless((SOURCE / 'r0.json').exists() and (ROOT / 'observed-results/report.json').exists(),
                     'external real dataset or generated report unavailable')
class ObservedTests(unittest.TestCase):
    def test_raw_reconciliation(self):
        report = json.loads((ROOT / 'observed-results/report.json').read_text())
        raw = {arm: json.loads((SOURCE / (arm + '.json')).read_text()) for arm in ['r0', 'r1', 'r3']}
        self.assertEqual(sum(map(len, raw.values())), 96)
        self.assertEqual(report['data_quality']['total_issues'], 0)
        self.assertEqual(report['tool']['sha256'], hashlib.sha256((ROOT / 'analyze.py').read_bytes()).hexdigest())
        for _, source in report['input_arms']:
            self.assertEqual(source['sha256'], hashlib.sha256((SOURCE / (source['label'] + '.json')).read_bytes()).hexdigest())
        for arm, records in raw.items():
            for cfg in ['solo:ABYSS', 'coop4:ABYSS']:
                rows = [r for r in records if r['config'] == cfg]
                st = report['arms'][arm]['by_config'][cfg]
                self.assertEqual(sorted(r['seed'] for r in rows), list(range(1, 17)))
                self.assertEqual((st['games'], st['wins']), (16, sum(r['victory'] for r in rows)))
                self.assertEqual(st['wins'], 0)
                self.assertAlmostEqual(st['wilson95'][1], 0.1936076805344365)
                self.assertEqual(st['errors_total'], sum(r['errors'] for r in rows))
                self.assertEqual(st['hidden_reached'], sum(r['hidden'] for r in rows))
                self.assertAlmostEqual(st['avg_failure_round'], sum(r['roundsPassed'] + 1 for r in rows) / 16)
                self.assertAlmostEqual(st['avg_wall_ms'], math.fsum(r['ms'] for r in rows) / 16)
                if arm == 'r0':
                    continue
                bm = {r['seed']: r for r in raw['r0'] if r['config'] == cfg}
                self.assertTrue(all((r['stageId'], r['bossId'], r['errors']) ==
                                    (bm[r['seed']]['stageId'], bm[r['seed']]['bossId'], 0) for r in rows))
                pr = report['pairs'][arm]['by_config'][cfg]
                self.assertEqual(pr['n_eligible_pairs'], 16)
                self.assertEqual(pr['outcomes'], dict(both_win=0, both_loss=16, loss_to_win=0, win_to_loss=0))
                self.assertEqual(pr['win_rate_difference'], 0)
                self.assertIsNone(pr['mcnemar_exact']['p_two_sided'])
