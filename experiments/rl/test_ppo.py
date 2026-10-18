import copy
import unittest

import torch
from torch.distributions import Categorical

from experiments.rl.common import ActorCritic, seed_all, tensorize, build_decision
from experiments.rl.ppo import Transition, compute_gae, clipped_objective, update
from experiments.rl.test_common import state


class PPOTests(unittest.TestCase):
    def setUp(self):
        seed_all(7)

    def test_gae_terminal_vs_truncation_and_no_reset_leakage(self):
        # First transition times out with a real next V=4. Second is a different game.
        adv, ret = compute_gae([1, 100], [2, 3], [4, 99], [False, True], [True, False], gamma=.9, lam=1)
        torch.testing.assert_close(adv, torch.tensor([2.6, 97.]))
        torch.testing.assert_close(ret, torch.tensor([4.6, 100.]))
        terminal, _ = compute_gae([1], [2], [100], [True], [False], gamma=.9)
        torch.testing.assert_close(terminal, torch.tensor([-1.]))
        chain, _ = compute_gae([0, 1], [0, 0], [0, 0], [False, True], [False, False], gamma=.9, lam=.5)
        torch.testing.assert_close(chain, torch.tensor([.45, 1.]))

    def test_clipping_both_advantage_signs_and_gradients(self):
        # Oversized positive ratio and undersized negative ratio are both clipped.
        ratio = torch.tensor([1.5, .5, 1.1, .9], requires_grad=True)
        loss, _ = clipped_objective(ratio.log(), torch.zeros(4), torch.tensor([1., -1., 1., -1.]), .2)
        self.assertAlmostEqual(float(loss.detach()), -.15, places=6)
        loss.backward()
        torch.testing.assert_close(ratio.grad, torch.tensor([0., 0., -.25, .25]))

    def test_update_exact_candidates_and_both_networks_change(self):
        model = ActorCritic()
        optimizer = torch.optim.Adam(model.parameters(), lr=.01)
        decision = build_decision(state())
        obs, candidates = tensorize(decision)
        records = []
        with torch.no_grad():
            for index in (0, 1, 2):
                # Different captured masks: the second row has only two candidates.
                captured = candidates[:2].clone() if index == 1 else candidates.clone()
                logits, value = model(obs, captured)
                logp = Categorical(logits=logits).log_prob(torch.tensor(index))
                records.append(Transition(obs.clone(), captured, index, float(logp), float(value),
                                          float(index-1), .5, index == 2, False))
        before = copy.deepcopy(model.state_dict())
        captured_before = [t.candidates.clone() for t in records]
        result = update(model, optimizer, records, epochs=2, batch_size=3)
        self.assertEqual(result['updates'], 2)
        self.assertGreater(result['gradient_norm'], 0)
        self.assertTrue(all(torch.isfinite(torch.tensor(v)) for v in result.values()))
        for name in ('actor', 'critic'):
            self.assertTrue(any(not torch.equal(v, before[k]) for k, v in model.state_dict().items() if k.startswith(name)))
        for t, captured in zip(records, captured_before):
            torch.testing.assert_close(t.candidates, captured)

    def test_rollout_boundary_bootstraps_and_one_candidate_has_zero_policy_gradient(self):
        adv, ret = compute_gae([0], [1], [2], [False], [False], gamma=.9)
        torch.testing.assert_close(ret, torch.tensor([1.8]))
        logit = torch.tensor([3.], requires_grad=True)
        loss = -Categorical(logits=logit).log_prob(torch.tensor(0))
        loss.backward()
        torch.testing.assert_close(logit.grad, torch.zeros(1))


if __name__ == '__main__':
    unittest.main()
