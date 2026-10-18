"""Small synthetic records exercise the public CLI, never represent training results."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import analyze


def row(seed, win=False, **overrides):
    rec = dict(config='solo:ABYSS', seed=seed, stageId='stage-A', bossId='boss-A',
               victory=win, hidden=False, roundsPassed=14 if win else 9,
               bossRound=14, errors=0, ms=100, lpLeft=0)
    rec.update(overrides)
    return rec


class AnalysisTests(unittest.TestCase):
    def run_report(self, base, cand, extra=(), manifest=None):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            p = Path(tmp)
            for name, rows in [('baseline', base), ('candidate', cand)]:
                (p / (name + '.json')).write_text(json.dumps(rows))
            cmd = [sys.executable, str(ROOT / 'analyze.py'), '--baseline',
                   'base=' + str(p / 'baseline.json'), '--candidate',
                   'cand=' + str(p / 'candidate.json'), '--out-dir', str(p / 'out')]
            if manifest is not None:
                (p / 'manifest.json').write_text(json.dumps(manifest))
                cmd += ['--manifest', str(p / 'manifest.json')]
            proc = subprocess.run(cmd + list(extra), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads((p / 'out/report.json').read_text()), (p / 'out/report.md').read_text()

    def test_exact_known_probabilities_and_wilson(self):
        self.assertEqual(analyze.mcnemar_exact(6, 0)['p_two_sided'], 0.03125)
        self.assertEqual(analyze.mcnemar_exact(5, 1)['p_two_sided'], 0.21875)
        self.assertEqual(analyze.mcnemar_exact(3, 3)['p_two_sided'], 1)
        self.assertIsNone(analyze.mcnemar_exact(0, 0)['p_two_sided'])
        self.assertAlmostEqual(analyze.wilson_ci(0, 16)[1], 0.1936076805344365)
        self.assertAlmostEqual(analyze.wilson_ci(8, 16)[0], 0.2799956361032601)

    def test_flips_and_unseen_groups(self):
        base = [row(i, i == 7) for i in range(1, 9)]
        cand = [row(i, i <= 6, hidden=i == 8, ms=200) for i in range(1, 9)]
        rep, md = self.run_report(base, cand, ['--train-seeds', '1-4', '--eval-seeds', '5-8'],
                                  {'jobs': 4, 'trained_model': False})
        p = rep['pairs']['cand']['by_config']['solo:ABYSS']
        self.assertEqual(p['outcomes'], dict(both_win=0, loss_to_win=6, win_to_loss=1, both_loss=1))
        self.assertEqual(p['win_rate_difference'], 5 / 8)
        self.assertEqual(p['mcnemar_exact']['p_two_sided'], 0.125)
        self.assertEqual([x['seed'] for x in p['loss_to_win']], list(range(1, 7)))
        self.assertEqual(p['win_to_loss'][0]['boss'], 'boss-A')
        self.assertEqual(p['seed_groups']['eval']['candidate_win_rate'], 0.5)
        st = rep['arms']['cand']['by_config']['solo:ABYSS']
        self.assertEqual((st['wins'], st['hidden_reached_but_loss'], st['avg_failure_round'], st['avg_wall_ms']), (6, 1, 10, 200))
        self.assertIn('jobs=4', md)
        self.assertIn('stage-A/boss-A', md)

    def test_dirty_pairing_never_silently_matches(self):
        base = [row(i) for i in range(1, 10)] + [row(2, victory='bad')]
        cand = [row(i, True) for i in range(1, 9)] + [row(10, True)]
        # Both sides missing the same identifiers MUST still be excluded.
        base[2]['stageId'] = cand[2]['stageId'] = None
        cand[3]['bossId'] = 'different'
        cand[4]['errors'] = 1
        del cand[5]['errors']
        cand[6]['errors'] = -1
        cand[7]['seed'] = None
        rep, md = self.run_report(base, cand)
        p = rep['pairs']['cand']['by_config']['solo:ABYSS']
        self.assertEqual(p['n_eligible_pairs'], 1)
        self.assertEqual(p['excluded']['duplicate_key_baseline'], [2])
        self.assertEqual(p['excluded']['missing_in_candidate'], [8, 9])
        self.assertEqual(p['excluded']['missing_in_baseline'], [10])
        self.assertEqual(len(p['excluded']['stage_boss_mismatch']), 2)
        self.assertEqual(len(p['excluded']['nonzero_or_unknown_errors']), 3)
        self.assertGreater(rep['data_quality']['total_issues'], 0)
        self.assertNotIn('未发现问题', md)
        self.assertEqual(rep['input_arms'][0][1]['dropped_records'], 1)

    def test_missing_only_affects_quality_and_unknown_is_not_false(self):
        b = row(1); del b['hidden']
        rep, md = self.run_report([b, row(2)], [row(1)])
        self.assertGreater(rep['data_quality']['pairing_issue_count'], 0)
        self.assertEqual(rep['arms']['base']['by_config']['solo:ABYSS']['hidden_unknown'], 1)
        self.assertIsNone(rep['arms']['base']['per_seed'][0]['hidden'])

    def test_zero_wins_coverage_and_pooled_dependence(self):
        recs = [row(i, config=c) for c in ['solo:ABYSS', 'coop4:ABYSS'] for i in [1, 2]]
        rep, md = self.run_report(recs, recs, manifest={
            'jobs': 1, 'configs': ['solo:ABYSS', 'coop4:ABYSS'],
            'seed_start': 1, 'seed_count_per_mode_per_arm': 3})
        self.assertTrue(rep['data_quality']['coverage_vs_manifest'])
        for p in rep['pairs']['cand']['by_config'].values():
            self.assertEqual(p['verdict_code'], 'uninformative_zero_wins')
            self.assertIsNone(p['difference_ci'])
        pooled = rep['pairs']['cand']['all_configs_pooled']
        self.assertEqual(pooled['n_eligible_pairs'], 4)
        self.assertEqual(pooled['verdict_code'], 'descriptive_only')
        self.assertIsNone(pooled['mcnemar_exact']['p_two_sided'])
        self.assertIn('不是“两臂等效”', md)

    def test_overlap_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            p = Path(tmp) / 'in.json'; p.write_text('[]')
            proc = subprocess.run([sys.executable, str(ROOT / 'analyze.py'), '--baseline', str(p),
                                   '--train-seeds', '1-3', '--eval-seeds', '3-5'], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('overlap', proc.stderr)


if __name__ == '__main__':
    unittest.main()
