"""Tests for experiments/rl/a2c.py (no game, no shared common needed).

Covers the A2C-specific math only:
- exact n-step returns: terminal zero-bootstrap vs truncated V(next) bootstrap
  vs interior chaining, per-step gamma;
- candidate binding: gradient flows into the parameters that produced the
  chosen candidate's logit/value from exact captured features (candidate
  index is not a stable identity across decisions);
- a2c_update: parameter changes, finite metrics, value learns a fixed target;
- non-finite loss is skipped without breaking the optimizer state.
"""
import math
import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import a2c  # noqa: E402


class TinyAC(torch.nn.Module):
    """Stand-in matching the common.ActorCritic forward contract:
    forward(obs[OBS_DIM], actions[N, ACTION_DIM]) -> (logits[N], scalar)."""

    def __init__(self, obs_dim=4, action_dim=3):
        super().__init__()
        self.obs_dim, self.action_dim = obs_dim, action_dim
        self.policy = torch.nn.Linear(obs_dim + action_dim, 1)
        self.value = torch.nn.Linear(obs_dim + action_dim, 1)

    def forward(self, obs, actions):
        n = actions.shape[0]
        obs_b = obs.unsqueeze(0).expand(n, -1)
        feats = torch.cat([obs_b, actions], dim=1)
        logits = self.policy(feats).squeeze(1)
        value = self.value(feats).mean()
        return logits, value


def make_batch():
    return [{"obs": torch.randn(4), "actions": torch.randn(5, 3),
             "index": 2, "return": 1.0}]


class NStepReturnsTest(unittest.TestCase):
    def test_terminal_zero_bootstrap(self):
        # single terminal step: return = reward exactly
        self.assertEqual(a2c.compute_nstep_returns([1.0], [0.0], [True], [False], 0.99),
                         [1.0])

    def test_truncated_bootstraps_next_value(self):
        # truncation must NOT count as terminal: r + gamma * V(next)
        self.assertAlmostEqual(
            a2c.compute_nstep_returns([0.5], [2.0], [False], [True], 0.9)[0],
            0.5 + 0.9 * 2.0, places=6)

    def test_interior_chains_per_step_gamma(self):
        # two interior steps: r0 + g*r1 + g^2*r2
        rets = a2c.compute_nstep_returns([1.0, 0.0, 2.0],
                                         [0.0, 0.0, 0.5],
                                         [False, False, False],
                                         [False, False, True], 0.5)
        self.assertAlmostEqual(rets[0], 1.0 + 0.5 * (0.0 + 0.5 * 2.25), places=6)
        self.assertAlmostEqual(rets[1], 0.0 + 0.5 * 2.25, places=6)
        self.assertAlmostEqual(rets[2], 2.0 + 0.5 * 0.5, places=6)

    def test_terminal_inside_window_cuts_chain(self):
        # steps: interior, terminal, post-terminal (never reached)
        rets = a2c.compute_nstep_returns([1.0, -1.0, 100.0],
                                         [0.0, 0.0, 0.0],
                                         [False, True, False],
                                         [False, False, True], 0.9)
        self.assertAlmostEqual(rets[0], 1.0 + 0.9 * -1.0, places=6)
        self.assertEqual(rets[1], -1.0)  # terminal: no bootstrap
        self.assertEqual(rets[2], 100.0)  # own step

    def test_window_end_bootstraps_chained_next_value(self):
        # a full n-step window ending nonterminal: last step bootstraps V(s+n)
        rets = a2c.compute_nstep_returns([1.0, 1.0], [0.0, 3.0],
                                         [False, False], [False, False], 0.5)
        self.assertAlmostEqual(rets[0], 1.0 + 0.5 * (1.0 + 0.5 * 3.0), places=6)
        self.assertAlmostEqual(rets[1], 1.0 + 0.5 * 3.0, places=6)


class SoftmaxBindingTest(unittest.TestCase):
    def test_entropy_of_uniform_distribution(self):
        logits = torch.zeros(5)
        _logp, probs, entropy = a2c.decision_logp_entropy(logits)
        self.assertTrue(torch.allclose(probs, torch.full((5,), 0.2)))
        self.assertAlmostEqual(float(entropy), math.log(5), places=5)

    def test_gradient_reaches_chosen_candidate_parameters(self):
        torch.manual_seed(0)
        model = TinyAC()
        obs = torch.randn(4)
        actions = torch.randn(5, 3)
        logits, value = model(obs, actions)
        logp, _probs, entropy = a2c.decision_logp_entropy(logits)
        chosen = logp[2]
        (chosen + value + entropy).backward()
        self.assertIsNotNone(model.policy.weight.grad)
        self.assertGreater(float(model.policy.weight.grad.abs().sum()), 0.0)
        self.assertIsNotNone(model.value.weight.grad)
        self.assertGreater(float(model.value.weight.grad.abs().sum()), 0.0)

    def test_chosen_index_changes_loss_direction(self):
        # candidate index is a position inside THIS decision: choosing a
        # different position must give a different log-prob on real features.
        torch.manual_seed(1)
        model = TinyAC()
        obs = torch.randn(4)
        actions = torch.randn(4, 3)
        with torch.no_grad():
            logits, _ = model(obs, actions)
            logp, _, _ = a2c.decision_logp_entropy(logits)
        self.assertFalse(torch.allclose(logp[0], logp[1]))
        self.assertFalse(torch.allclose(logp[1], logp[2]))


class A2CUpdateTest(unittest.TestCase):
    def test_update_changes_parameters_and_reports_finite_metrics(self):
        torch.manual_seed(2)
        model = TinyAC()
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
        before = {n: p.detach().clone() for n, p in model.named_parameters()}
        out = a2c.a2c_update(model, optimizer, make_batch(),
                             gamma=0.99, vf_coef=0.5, ent_coef=0.01,
                             max_grad_norm=0.5)
        self.assertIsNotNone(out)
        for key in ("policy_loss", "value_loss", "entropy", "grad_norm"):
            self.assertTrue(math.isfinite(out[key]), key)
        changed = sum(
            1 for n, p in model.named_parameters()
            if not torch.equal(before[n], p.detach()))
        self.assertGreater(changed, 0)

    def test_value_head_learns_fixed_target(self):
        torch.manual_seed(3)
        model = TinyAC()
        optimizer = torch.optim.Adam(model.parameters(), lr=5e-2)
        obs = torch.zeros(4)
        actions = torch.zeros(5, 3)
        batch = [{"obs": obs, "actions": actions, "index": 0, "return": 2.0}]
        for _ in range(400):
            out = a2c.a2c_update(model, optimizer, batch, gamma=0.99,
                                 vf_coef=1.0, ent_coef=0.0, max_grad_norm=5.0)
        self.assertIsNotNone(out)
        with torch.no_grad():
            _logits, value = model(obs, actions)
        self.assertLess(abs(float(value) - 2.0), 0.2)

    def test_nonfinite_loss_is_skipped(self):
        model = TinyAC()
        for p in model.parameters():
            torch.nn.init.zeros_(p)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
        # advantage=0 and zero logits/params -> zero loss stays finite, so
        # instead poison one parameter to NaN and verify the skip path.
        with torch.no_grad():
            model.value.weight.fill_(float("nan"))
        before = {n: p.detach().clone() for n, p in model.named_parameters()}
        out = a2c.a2c_update(model, optimizer, make_batch(), gamma=0.99,
                             vf_coef=0.5, ent_coef=0.01, max_grad_norm=0.5)
        self.assertIsNone(out)
        for n, p in model.named_parameters():
            # NaN-safe equality: no parameter may have been touched.
            same = ((before[n] == p.detach())
                    | (torch.isnan(before[n]) & torch.isnan(p.detach())))
            self.assertTrue(bool(same.all()), n)


if __name__ == "__main__":
    unittest.main()
