"""Independent arithmetic and behavior-policy checks; no game process."""
import copy
import struct
import unittest
from unittest import mock

import train
import torch


CONFIG = {'gamma': 1., 'gae_lambda': 1., 'epochs': 2, 'batch_size': 2,
          'clip': .2, 'value_coef': .5, 'entropy_coef': .01, 'max_grad_norm': .5}


def trajectory(rewards, dones, ends, next_values, values=None):
    n = len(rewards)
    return {'features': [None] * n, 'actions': [0] * n, 'logprobs': [0.] * n,
            'values': values or [0.] * n, 'rewards': rewards, 'dones': dones,
            'ends': ends, 'next_values': next_values}


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(33)
        self.model = train.ActorCritic(2, 1, 8)
        self.row = trajectory([1., -1., .5, 2.], [False] * 3 + [True],
                              [False] * 3 + [True], [0.] * 4)
        self.row['features'] = [
            {'global': [1., 0.], 'candidates': [[1.], [200.], [-1.]],
             'mask': [True, False, True]},
            {'global': [0., 1.], 'candidates': [[1.]], 'mask': [True]},
            {'global': [.5, 1.], 'candidates': [[-1.], [2.]], 'mask': [True, True]},
            {'global': [1., .5], 'candidates': [[1.], [-2.]], 'mask': [True, True]},
        ]
        self.row['actions'], self.row['logprobs'], self.row['values'] = map(
            list, zip(*(self.model.act(f) for f in self.row['features'])))
        self.results = [{'worker': 0, 'trajectory': self.row}]

    def old_bytes(self):
        return struct.pack('4d', *self.row['logprobs'])

    def run_update(self, model, batch_offsets=None, singleton_offsets=None, reject=False):
        # Inject only into the no-grad synchronization check. Actual actor sampling,
        # masked distributions, gradients and Adam updates use production code.
        forward = model.forward
        randperm = torch.randperm
        singleton_rows, batch_errors = [], []
        old_bytes = self.old_bytes()
        old_object = self.row['logprobs']
        parameters = train.vector(model)
        rng = torch.get_rng_state().clone()
        optimizer = torch.optim.Adam(model.parameters(), lr=.01)
        features = self.row['features']
        positions = {id(f): i for i, f in enumerate(features)}

        def checked_forward(fs):
            distribution, value = forward(fs)
            if not torch.is_grad_enabled():
                indices = [positions[id(f)] for f in fs]
                singleton = len(fs) == 1
                if singleton:
                    singleton_rows.extend(indices)
                offsets = (singleton_offsets if singleton else batch_offsets) or {}
                log_prob = distribution.log_prob

                def perturbed(actions):
                    result = log_prob(actions)
                    result = result + torch.tensor([offsets.get(i, 0.) for i in indices])
                    if not singleton:
                        old = torch.tensor([self.row['logprobs'][i] for i in indices])
                        batch_errors.append((result - old).abs().max().item())
                    return result

                distribution.log_prob = perturbed
            return distribution, value

        def checked_randperm(*args, **kwargs):
            if shuffle.call_count == 1:
                # This is the exact boundary between validation and PPO. Fallback
                # must neither sample an action nor mutate parameters/oldprobs.
                self.assertTrue(torch.equal(torch.get_rng_state(), rng))
                self.assertTrue(torch.equal(train.vector(model), parameters))
                self.assertEqual(self.old_bytes(), old_bytes)
            return randperm(*args, **kwargs)

        with mock.patch.object(model, 'forward', side_effect=checked_forward), \
                mock.patch.object(torch, 'randperm', side_effect=checked_randperm) as shuffle, \
                mock.patch.object(optimizer, 'step', wraps=optimizer.step) as step:
            if reject:
                with self.assertRaisesRegex(RuntimeError, 'Behavior policy mismatch'):
                    train.update(model, optimizer, self.results, CONFIG)
                step.assert_not_called()
                shuffle.assert_not_called()
                self.assertEqual(optimizer.state_dict()['state'], {})
                self.assertTrue(torch.equal(train.vector(model), parameters))
                self.assertTrue(torch.equal(torch.get_rng_state(), rng))
                metrics = None
            else:
                metrics = train.update(model, optimizer, self.results, CONFIG)
                self.assertEqual(step.call_count, 4)
        self.assertIs(self.row['logprobs'], old_object)
        self.assertEqual(self.old_bytes(), old_bytes)
        return metrics, singleton_rows, batch_errors, optimizer.state_dict()

    def assert_state_equal(self, left, right):
        if isinstance(left, torch.Tensor):
            self.assertTrue(torch.equal(left, right))
        elif isinstance(left, dict):
            self.assertEqual(left.keys(), right.keys())
            for key in left:
                self.assert_state_equal(left[key], right[key])
        elif isinstance(left, list):
            self.assertEqual(len(left), len(right))
            for a, b in zip(left, right):
                self.assert_state_equal(a, b)
        else:
            self.assertEqual(left, right)

    def test_batch_only_roundoff_falls_back_without_changing_ppo(self):
        reference = copy.deepcopy(self.model)
        rng = torch.get_rng_state().clone()
        normal, rows, errors, normal_optimizer = self.run_update(reference)
        self.assertEqual(rows, [])
        self.assertEqual(normal['behaviorLogprobMaxError'], max(errors))
        final_rng = torch.get_rng_state().clone()
        torch.set_rng_state(rng)
        repaired, rows, errors, repaired_optimizer = self.run_update(
            self.model, batch_offsets={0: 3e-5})
        self.assertFalse(normal['behaviorLogprobFallbackUsed'])
        self.assertIsNone(normal['behaviorLogprobSingletonMaxError'])
        self.assertEqual(normal['behaviorLogprobAcceptedMaxError'], normal['behaviorLogprobMaxError'])
        self.assertEqual(rows, list(range(4)))  # Includes the unaffected second batch.
        self.assertTrue(repaired['behaviorLogprobFallbackUsed'])
        self.assertEqual(repaired['behaviorLogprobMaxError'], max(errors))
        self.assertGreater(max(errors), 2e-5)
        self.assertEqual(repaired['behaviorLogprobSingletonMaxError'], 0.)
        self.assertEqual(repaired['behaviorLogprobAcceptedMaxError'], 0.)
        for key in normal:
            if not key.startswith('behaviorLogprob'):
                self.assertEqual(normal[key], repaired[key])
        self.assertTrue(torch.equal(train.vector(reference), train.vector(self.model)))
        self.assertTrue(torch.equal(final_rng, torch.get_rng_state()))
        self.assert_state_equal(normal_optimizer, repaired_optimizer)

    def test_finite_batch_within_original_tolerance_stays_on_fast_path(self):
        metrics, rows, errors, _ = self.run_update(self.model, batch_offsets={0: 1e-5})
        self.assertEqual(rows, [])
        self.assertGreater(max(errors), 0.)
        self.assertLessEqual(max(errors), 2e-5)
        self.assertFalse(metrics['behaviorLogprobFallbackUsed'])
        self.assertEqual(metrics['behaviorLogprobAcceptedMaxError'], max(errors))

    def test_singleton_reports_nonzero_error_with_original_tolerance(self):
        reference = copy.deepcopy(self.model)
        rng = torch.get_rng_state().clone()
        normal, _, _, normal_optimizer = self.run_update(reference)
        torch.set_rng_state(rng)
        metrics, rows, _, repaired_optimizer = self.run_update(
            self.model, batch_offsets={0: 3e-5}, singleton_offsets={3: 1e-5})
        self.assertEqual(rows, list(range(4)))
        self.assertGreater(metrics['behaviorLogprobSingletonMaxError'], 0.)
        self.assertLessEqual(metrics['behaviorLogprobSingletonMaxError'], 2e-5)
        self.assertEqual(metrics['behaviorLogprobAcceptedMaxError'],
                         metrics['behaviorLogprobSingletonMaxError'])
        # Even an accepted nonzero singleton error must not change PPO's old
        # denominator: replacing it would change gradients and Adam's state.
        self.assertEqual(metrics['loss'], normal['loss'])
        self.assertTrue(torch.equal(train.vector(reference), train.vector(self.model)))
        self.assert_state_equal(normal_optimizer, repaired_optimizer)

    def test_late_singleton_mismatch_cannot_be_hidden_by_first_batch_fallback(self):
        _, rows, _, _ = self.run_update(self.model, batch_offsets={0: 3e-5},
                                         singleton_offsets={3: 3e-5}, reject=True)
        self.assertEqual(rows, list(range(4)))

    def test_nonfinite_batch_never_falls_back_even_if_later_batch_is_finite(self):
        for bad in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(bad=bad):
                _, rows, _, _ = self.run_update(self.model, batch_offsets={0: bad}, reject=True)
                self.assertEqual(rows, [])

    def test_nonfinite_singleton_is_rejected(self):
        for bad in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(bad=bad):
                self.run_update(self.model, batch_offsets={0: 3e-5},
                                singleton_offsets={0: bad}, reject=True)

    def test_genuine_parameter_drift_is_rejected(self):
        with torch.no_grad():
            self.model.actor[0].weight.mul_(2.)
        self.run_update(self.model, reject=True)

    def test_corrupt_old_logprob_is_rejected_without_overwrite(self):
        for bad in (self.row['logprobs'][0] + 3e-5, float('nan'), float('inf')):
            with self.subTest(bad=bad):
                self.row['logprobs'][0] = bad
                self.run_update(self.model, reject=True)

    def test_changed_legal_mask_is_rejected(self):
        original = self.row['features'][-1]['mask'][:]
        selected = self.row['actions'][-1]
        # Removing a sampled action yields -inf; removing the other legal action
        # yields a finite but genuinely different singleton probability.
        for removed in (selected, 1 - selected):
            with self.subTest(removed=removed):
                mask = original[:]
                mask[removed] = False
                self.row['features'][-1]['mask'] = mask
                self.run_update(self.model, reject=True)


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
