"""Meaningful smoke tests for reinforce.py.

Runs without common.py (return math, outcome classification, finite
gradient / parameter-change updates via a tiny scorer with the same
forward signature). Candidate-binding / dynamic-count tests run only when
the shared common.py contract implementation is present.
"""

import math
import os
import sys
import unittest

import torch

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_HERE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import reinforce


class TinyScorer(torch.nn.Module):
    """Minimal candidate scorer with the shared ActorCritic signature:
    forward(obs_tensor, action_tensor) -> (logits[N], scalar value)."""

    def __init__(self, obs_dim=3, act_dim=4):
        super().__init__()
        self.w = torch.nn.Parameter(torch.randn(act_dim))
        self.b = torch.nn.Parameter(torch.zeros(1))
        self.v = torch.nn.Parameter(torch.randn(obs_dim))

    def forward(self, obs, actions):
        logits = actions @ self.w + self.b
        value = (obs * self.v).sum()
        return logits, value


def fake_episode(model, truncated=False, seed=0):
    torch.manual_seed(seed)
    ep = {"logprobs": [], "values": [], "entropies": [], "rewards": [],
          "steps": 0, "terminated": not truncated, "truncated": truncated,
          "failed": False, "bootstrap": 0.25 if truncated else 0.0}
    for n_candidates, reward in ((2, 0.0), (3, 0.1), (2, 1.0)):
        obs_t = torch.rand(3)
        act_t = torch.rand(n_candidates, 4)
        choice = reinforce.sample_action(model, obs_t, act_t)
        assert choice["index"] < n_candidates
        ep["logprobs"].append(choice["logprob"])
        ep["values"].append(choice["value"])
        ep["entropies"].append(choice["entropy"])
        ep["rewards"].append(reward)
        ep["steps"] += 1
    return ep


class TestReturns(unittest.TestCase):
    def test_terminated_returns(self):
        out = reinforce.returns([0.0, 0.0, 1.0], 0.9, bootstrap=0.0)
        self.assertEqual(out, [0.81, 0.9, 1.0])

    def test_truncated_bootstrap_returns(self):
        out = reinforce.returns([0.0, 1.0], 0.9, bootstrap=0.5)
        self.assertAlmostEqual(out[-1], 1.0 + 0.9 * 0.5)
        self.assertAlmostEqual(out[0], 0.9 * out[-1])

    def test_bootstrap_propagates_from_tail(self):
        # G_T = r_T + gamma*V(s_T); the bootstrap enters at the tail and
        # decays into earlier returns by gamma per step.
        with_boot = reinforce.returns([1.0, 1.0], 0.99, bootstrap=1.0)
        no_boot = reinforce.returns([1.0, 1.0], 0.99, bootstrap=0.0)
        self.assertAlmostEqual(with_boot[1] - no_boot[1], 0.99)
        self.assertAlmostEqual(with_boot[0] - no_boot[0], 0.99 ** 2)


class TestOutcomeClassification(unittest.TestCase):
    def test_truncated_never_a_win_even_with_positive_return(self):
        self.assertIsNone(reinforce.classify_outcome(False, True, 5.0))

    def test_terminated_win_and_loss(self):
        self.assertEqual(reinforce.classify_outcome(True, False, 1.0), "win")
        self.assertEqual(reinforce.classify_outcome(True, False, -1.0), "loss")

    def test_failed_not_counted(self):
        self.assertIsNone(reinforce.classify_outcome(False, False, 1.0))

    def test_adapter_victory_takes_precedence_over_reward_sign(self):
        # Terminal reward mixes in -potential(prev); a defeat could still
        # carry a small positive terminal reward in ablations.
        self.assertEqual(
            reinforce.classify_outcome(True, False, 0.05, victory=False),
            "loss")
        self.assertEqual(
            reinforce.classify_outcome(True, False, -0.05, victory=True),
            "win")

    def test_technical_failure_never_a_win(self):
        self.assertIsNone(
            reinforce.classify_outcome(False, True, 5.0, victory=True))


class TestFiniteGradientAndParameterChange(unittest.TestCase):
    def _check_update(self, truncated):
        torch.manual_seed(1)
        model = TinyScorer()
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
        before = [p.detach().clone() for p in model.parameters()]
        ep = fake_episode(model, truncated=truncated, seed=truncated + 2)
        stats = reinforce.update(model, optimizer, ep, gamma=0.99)
        self.assertIsNotNone(stats)
        for key in ("loss", "policy_loss", "value_loss", "entropy",
                    "mean_return"):
            self.assertTrue(math.isfinite(stats[key]), key)
        self.assertTrue(stats["finite_grads"])
        for p, b in zip(model.parameters(), before):
            delta = (p.detach() - b).abs().max().item()
            self.assertGreater(delta, 0.0)  # optimizer step changed params
        return stats

    def test_terminated_episode_updates(self):
        self._check_update(truncated=False)

    def test_truncated_episode_bootstrapped_update(self):
        stats = self._check_update(truncated=True)
        self.assertTrue(math.isfinite(stats["mean_return"]))

    def test_baseline_reduces_policy_signal_when_values_match_returns(self):
        # If V(s) == G exactly, advantages are 0 and policy grad vanishes,
        # but the critic loss is 0 too: the update stays finite.
        torch.manual_seed(3)
        model = TinyScorer()
        ep = fake_episode(model, seed=9)
        ep["values"] = [torch.tensor(g, requires_grad=True)
                        for g in reinforce.returns(ep["rewards"], 0.99)]
        ep["logprobs"] = [lp.detach().requires_grad_() for lp in ep["logprobs"]]
        ep["entropies"] = [e.detach().requires_grad_() for e in ep["entropies"]]
        stats = reinforce.update(model, torch.optim.Adam(model.parameters()), ep, 0.99)
        self.assertAlmostEqual(stats["value_loss"], 0.0, places=6)
        self.assertAlmostEqual(stats["policy_loss"], 0.0, places=6)


@unittest.skipIf(reinforce.common is None,
                 "common.py not present next to reinforce.py yet")
class TestCandidateBinding(unittest.TestCase):
    def test_dynamic_candidate_count_and_logprob_binding(self):
        c = reinforce.common
        torch.manual_seed(0)
        model = c.ActorCritic()
        for n in (2, 3):
            feats = [[0.0] * c.ACTION_DIM for _ in range(n)]
            for i in range(n):
                feats[i][i] = 1.0  # distinct candidates, exact binding
            d = c.Decision(
                obs=[0.1] * c.OBS_DIM,
                action_features=feats,
                actions=[{"uid": i} for i in range(n)],
                terminal=False,
            )
            obs_t, act_t = c.tensorize(d)
            self.assertEqual(act_t.shape[0], n)  # dynamic candidate count
            logits, value = model(obs_t, act_t)
            self.assertEqual(logits.shape[0], n)
            self.assertEqual(value.ndim, 0)  # scalar state value
            choice = reinforce.sample_action(model, obs_t, act_t)
            self.assertLess(choice["index"], n)
            manual = torch.distributions.Categorical(logits=logits).log_prob(
                torch.tensor(choice["index"]))
            # exact sampled features/logprob correspondence
            self.assertTrue(torch.allclose(choice["logprob"], manual))
            self.assertTrue(torch.isfinite(choice["entropy"]))
            self.assertTrue(torch.isfinite(choice["value"]))


if __name__ == "__main__":
    unittest.main()
