"""Independent arithmetic and behavior-policy checks; no game process."""
import copy
import unittest

import train
import torch


CONFIG = {'gamma': 1., 'gae_lambda': 1., 'epochs': 2, 'batch_size': 2,
          'clip': .2, 'value_coef': .5, 'entropy_coef': .01, 'max_grad_norm': .5}


def trajectory(rewards, dones, ends, next_values, values=None):
    n = len(rewards)
    return {'features': [None] * n, 'actions': [0] * n, 'logprobs': [0.] * n,
            'values': values or [0.] * n, 'rewards': rewards, 'dones': dones,
            'ends': ends, 'next_values': next_values}


class NumericalTests(unittest.TestCase):
    def test_independent_streams_terminal_and_explicit_truncation(self):
        # Independent hand calculation: worker 0 returns 1+2, 2; worker 1's
        # time-limit episode returns 10+V(final)=14, next episode 20+V(next)=28.
        a = trajectory([1., 2.], [False, True], [False, True], [.25, 999.], [.5, .25])
        b = trajectory([10., 20.], [False, False], [True, False], [4., 8.])
        rows = [{'worker': 1, 'trajectory': b}, {'worker': 0, 'trajectory': a}]
        merged, advantages, returns = train.merge_trajectories(rows, CONFIG)
        torch.testing.assert_close(returns, torch.tensor([3., 2., 14., 28.]))
        torch.testing.assert_close(advantages, torch.tensor([2.5, 1.75, 14., 28.]))
        self.assertEqual(merged['rewards'], [1., 2., 10., 20.])
        b['rewards'][0] = 9999.
        _, changed, _ = train.merge_trajectories(rows, CONFIG)
        torch.testing.assert_close(changed[:2], advantages[:2])
        torch.testing.assert_close(changed[3:], advantages[3:])

    def test_sampling_cut_is_not_terminal(self):
        row = trajectory([1., 2.], [False, False], [False, False], [.25, 4.], [.5, .25])
        _, adv, returns = train.merge_trajectories([{'worker': 0, 'trajectory': row}],
                                                 {**CONFIG, 'gamma': .5})
        torch.testing.assert_close(returns, torch.tensor([3., 4.]))
        torch.testing.assert_close(adv, torch.tensor([2.5, 3.75]))

    def test_nontrivial_lambda(self):
        row = trajectory([1., 2.], [False, False], [False, False], [.25, 4.], [.5, .25])
        # delta1=3.75, delta0=.625; A0=.625 + .5*.4*3.75 = 1.375.
        _, adv, _ = train.merge_trajectories([{'worker': 0, 'trajectory': row}],
                                          {**CONFIG, 'gamma': .5, 'gae_lambda': .4})
        torch.testing.assert_close(adv, torch.tensor([1.375, 3.75]))

    def test_mask_and_old_logprob_survive_update(self):
        torch.manual_seed(33)
        model = train.ActorCritic(2, 1, 8)
        features = [
            {'global': [1., 0.], 'candidates': [[1.], [200.], [-1.]], 'mask': [True, False, True]},
            {'global': [0., 1.], 'candidates': [[1.]], 'mask': [True]},
        ]
        distribution, _ = model(features)
        self.assertEqual(distribution.probs[0, 1].item(), 0.)
        self.assertEqual(distribution.probs[1, 1:].sum().item(), 0.)
        for _ in range(16):
            actions = distribution.sample()
            self.assertIn(actions[0].item(), (0, 2))
            self.assertEqual(actions[1].item(), 0)
        row = trajectory([1., -1.], [False, True], [False, True], [0., 0.])
        row['features'] = features
        row['actions'], row['logprobs'], row['values'] = map(list, zip(*(model.act(f) for f in features)))
        old = copy.deepcopy(row['logprobs'])
        before = train.vector(model)
        optimizer = torch.optim.Adam(model.parameters(), lr=.01)
        result = train.update(model, optimizer, [{'worker': 0, 'trajectory': row}], CONFIG)
        self.assertGreater(result['parameterDeltaL2'], 0.)
        self.assertFalse(torch.equal(before, train.vector(model)))
        self.assertEqual(row['logprobs'], old)
        after, _ = model(features)
        self.assertGreater((after.log_prob(torch.tensor(row['actions'])) - torch.tensor(old)).abs().max().item(), 0.)
        changed = train.vector(model)
        with self.assertRaisesRegex(RuntimeError, 'Behavior policy mismatch'):
            train.update(model, optimizer, [{'worker': 0, 'trajectory': row}], CONFIG)
        torch.testing.assert_close(train.vector(model), changed)

    def test_seed_boundaries(self):
        self.assertEqual(train.seed_ranges(1, 2, 4096)[1], {'worker': 1, 'first': 4097, 'last': 8192})
        for base, workers, episodes in [(90001, 1, 1), (89999, 1, 3), (100001, 1, 1),
                                         (85905, 2, 1), (1, 1, 4097), (0, 1, 1),
                                         (0xffffffff, 1, 2)]:
            with self.subTest(base=base, workers=workers, episodes=episodes):
                with self.assertRaises(ValueError):
                    train.seed_ranges(base, workers, episodes)

    def test_checkpoint_schema_rules_dimensions(self):
        state = {'schema': {'version': 1}, 'rules': 'fixed',
                 'features': {'global': [0., 1.], 'candidates': [[1.]]}}
        checkpoint = {'schema': state['schema'], 'rules': state['rules'], 'dimensions': [2, 1, 8]}
        train.validate_checkpoint(checkpoint, state)
        for key, value in [('schema', {}), ('rules', 'wrong'), ('dimensions', [3, 1, 8])]:
            with self.assertRaises(ValueError):
                train.validate_checkpoint({**checkpoint, key: value}, state)


if __name__ == '__main__':
    unittest.main()
