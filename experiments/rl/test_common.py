"""Public-interface fixtures: no hidden state or game seeds required."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from experiments.rl import common as c


def state(*, terminal=False, can_act=True, alive=True):
    return {'terminal': terminal, 'revision': 1, 'round': 2, 'phase': 'PREP', 'can_act': can_act,
            'public': {'factions': ['TIMES', 'DOT'], 'teamLp': 5},
            'own': {'alive': alive, 'funds': 10, 'lp': 3, 'board': [], 'hand': [None, None], 'temp': [],
                    'shop': {'slots': [{'id': 'alpha', 'kind': 'chess', 'price': 3},
                                       {'id': 'beta', 'kind': 'chess', 'price': 4}]},
                    'bonds': [], 'canReady': True},
            'actions': [{'t': 'g.buy', 'targets': {'slot': [0, 1]}},
                        {'t': 'g.ready', 'targets': {'ready': [False, True]}}],
            'outcome': {'team': {'victory': False}, 'learner': {'alive': False}} if terminal else None}


class FakeSession:
    initial = state()
    replies = []
    instances = []
    def __init__(self, **config):
        self.config = config
        self.state = copy.deepcopy(self.initial)
        self.queue = copy.deepcopy(self.replies)
        self.closed = False
        self._child = None
        self.calls = []
        self.__class__.instances.append(self)
    def __enter__(self):
        return self
    def observe(self):
        return self.state
    def act(self, action):
        self.calls.append(action)
        item = self.queue.pop(0) if self.queue else {'accepted': False, 'state': self.state}
        if isinstance(item, Exception):
            raise item
        self.state = item['state']
        return item
    def wait(self):
        self.calls.append({'type': 'wait'})
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            self.state = item['state']
        return self.state
    def close(self):
        self.closed = True


class CommonTests(unittest.TestCase):
    def setUp(self):
        c.seed_all(0)
        FakeSession.initial, FakeSession.replies, FakeSession.instances = state(), [], []

    def test_dimensions_identity_and_exact_mask(self):
        s = state()
        d = c.build_decision(s)
        obs, actions = c.tensorize(d)
        self.assertEqual(tuple(obs.shape), (c.OBS_DIM,))
        self.assertEqual(tuple(actions.shape), (3, c.ACTION_DIM))
        model = c.ActorCritic()
        logits, value = model(obs, actions)
        permutation = torch.tensor([2, 0, 1])
        permuted, _ = model(obs, actions[permutation])
        torch.testing.assert_close(permuted, logits[permutation])
        masked = c.build_decision(s, blocked={c.action_key(d.actions[0])})
        self.assertEqual(masked.actions, d.actions[1:])
        self.assertEqual(masked.action_features, d.action_features[1:])
        self.assertEqual(c.tensorize(masked)[1].shape[0], 2)
        self.assertEqual(value.ndim, 0)
        # Candidate slot meanings follow visible content, not a slot-specific output head.
        changed = copy.deepcopy(s)
        changed['own']['shop']['slots'].reverse()
        self.assertNotEqual(c.build_decision(changed).action_features[0], d.action_features[0])

    def test_move_binding_draft_items_and_no_autoplay(self):
        s = state()
        s['own']['hand'] = [{'uid': 42, 'id': 'rook', 'kind': 'chess', 'items': []}, None,
                            {'uid': 77, 'id': 'hat', 'kind': 'item'}]
        s['actions'] += [
            {'t': 'g.move', 'targets': {'uid': [42], 'dir': ['UP', 'RIGHT'],
                                       'board': {'rows': [1, 2], 'cols': [1, 2]}}},
            {'t': 'g.equip', 'targets': {'itemUid': [77], 'targetUid': [42]}},
            {'t': 'g.reward', 'targets': {'idx': [0]}}]
        d = c.build_decision(s)
        moves = [a for a in d.actions if a['type'] == 'move']
        self.assertTrue(moves)
        self.assertTrue(all(a['uid'] == 42 and a['revision'] == 1 for a in moves))
        self.assertTrue(any(a['type'] == 'equip' and a['itemUid'] == 77 and a['targetUid'] == 42 for a in d.actions))
        self.assertFalse(any(a['type'] in ('wait', 'autoplay') for a in d.actions))
        s['actions'].append({'t': 'g.choice', 'targets': {'idx': [0, 1], 'choiceId': ['choose-now']}})
        self.assertTrue(all(a['type'] == 'choice' and a['choiceId'] == 'choose-now' for a in c.build_decision(s).actions))
        s['phase'] = 'BAND_DRAFT'
        s['actions'] = [{'t': 'g.band', 'targets': {'bandId': ['a', 'b']}}]
        self.assertEqual([a['bandId'] for a in c.build_decision(s).actions], ['a', 'b'])

    def test_choice_features_follow_displayed_card_not_index(self):
        s = state()
        s['actions'] = [{'t': 'g.choice', 'targets': {'idx': [0, 1], 'choiceId': ['pending']}}]
        s['own']['personalChoice'] = {'cards': [{'id': 'heal-card'}, {'id': 'gold-card'}]}
        first = c.build_decision(s)
        s['own']['personalChoice']['cards'].reverse()
        second = c.build_decision(s)
        self.assertEqual(first.actions, second.actions)
        self.assertNotEqual(first.action_features[0], second.action_features[0])

    def test_visible_deployment_cells_exclude_other_geometry(self):
        s = state()
        s['own']['hand'] = [{'uid': 8, 'id': 'piece', 'kind': 'chess'}, None]
        s['own']['deployment'] = {'field': 'normal', 'cells': [
            {'row': 7, 'col': 2, 'kind': 'melee'}, {'row': 8, 'col': 4, 'kind': 'ranged'}]}
        s['actions'].append({'t': 'g.move', 'targets': {'uid': [8], 'dir': ['UP'],
                            'board': {'rows': [0, 12], 'cols': [0, 6]}}})
        d = c.build_decision(s)
        board_moves = [a for a in d.actions if a['type'] == 'move' and a['to']['area'] == 'board']
        self.assertEqual({(a['to']['row'], a['to']['col']) for a in board_moves}, {(7, 2), (8, 4)})
        s['own']['deployment']['cells'] = []
        self.assertFalse(any(a['type'] == 'move' and a['to']['area'] == 'board'
                             for a in c.build_decision(s).actions))

    def test_critic_and_actor_gradients(self):
        model = c.ActorCritic()
        logits, value = model(*c.tensorize(c.build_decision(state())))
        loss = -torch.log_softmax(logits, 0)[0] + (value-2).square()
        loss.backward()
        for module in (model.actor, model.critic):
            self.assertGreater(sum(float(p.grad.abs().sum()) for p in module.parameters()), 0)

    def test_potential_max_active_core_and_discount_telescoping(self):
        a, b, terminal = state(), state(), state(terminal=True)
        b['own']['bonds'] = [{'bondId': 'yanShip', 'active': True, 'layers': 50},
                             {'bondId': 'off', 'active': True, 'layers': 99999},
                             {'bondId': 'yanShip', 'active': False, 'layers': 99999}]
        self.assertEqual(c._core_layers(b), 50)
        self.assertEqual(c.potential(terminal), 0)
        gamma = .9
        r0, r1 = gamma*c.potential(b)-c.potential(a), gamma*c.potential(terminal)-c.potential(b)
        self.assertAlmostEqual(r0+gamma*r1, -c.potential(a))
        self.assertLessEqual(c.potential(b), .2)

    @patch.object(c, 'Session', FakeSession)
    def test_rejection_mask_only_same_fingerprint_and_seed_reset(self):
        env = c.PlayerEnv(seed=11, reward_mode='sparse')
        d = env.reset()
        session = env.session
        selected = d.actions[0]
        next_d, r, term, trunc, info = env.step(0)
        self.assertNotIn(selected, next_d.actions)
        self.assertEqual(session.calls[0], selected)
        self.assertEqual((r, term, trunc, info['seed']), (0., False, False, 11))
        changed = state()
        changed['revision'] = 2
        session.queue = [{'accepted': True, 'state': changed}]
        d, *_ = env.step(0)
        self.assertEqual(len(d.actions), 3)
        env.reset()
        self.assertTrue(session.closed)
        self.assertEqual(env.seed, 12)
        env.reset(seed=40)
        self.assertEqual(env.seed, 40)
        env.close()
        self.assertEqual(FakeSession.instances[-1].config['max_requests'], 8192)

    @patch.object(c, 'Session', FakeSession)
    def test_eliminated_learner_waits_until_natural_team_terminal(self):
        FakeSession.initial = state(can_act=False, alive=False)
        end = state(terminal=True, can_act=False, alive=False)
        end['outcome']['team']['victory'] = True
        FakeSession.replies = [{'state': end}]
        env = c.PlayerEnv(reward_mode='sparse')
        d = env.reset()
        session = env.session
        self.assertEqual(d.actions[0]['type'], 'wait')
        d, reward, term, trunc, info = env.step(0)
        self.assertEqual((reward, term, trunc, d.terminal), (1., True, False, True))
        self.assertEqual(d.actions, [])
        self.assertTrue(session.closed)
        self.assertFalse(info['technical_failure'])

    @patch.object(c, 'Session', FakeSession)
    def test_step_request_no_progress_timeout_cleanup(self):
        for cause in ('steps', 'requests', 'timeout', 'no_progress', 'wall'):
            env = c.PlayerEnv(max_steps=1 if cause == 'steps' else 200)
            env.reset()
            session = env.session
            if cause == 'requests':
                env.requests = 8192
            elif cause == 'timeout':
                session.queue = [TimeoutError('fixture')]
            elif cause == 'no_progress':
                env.no_progress = 63
            elif cause == 'wall':
                env.deadline = 0
            d, reward, term, trunc, info = env.step(0)
            self.assertFalse(term)
            self.assertTrue(trunc)
            self.assertFalse(d.terminal)
            self.assertTrue(session.closed)
            self.assertEqual(info['technical_failure'], cause in ('requests', 'timeout'))

    def test_export_refuses_changed_checkpoint_and_sources_before_game(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = c.finish_training(directory, c.ActorCritic(), {'algorithm': 'ppo', 'actual_steps': 0, 'episodes': 0})
            checkpoint, policy = Path(paths['checkpoint']), Path(paths['policy'])
            spec = importlib.util.spec_from_file_location('test_exported_policy', policy)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            class TerminalGame:
                def observe(self):
                    return {'terminal': True}
            module.play(TerminalGame())
            self.assertIsNotNone(module._MODEL)
            checkpoint.write_bytes(checkpoint.read_bytes()+b'changed')
            with self.assertRaisesRegex(RuntimeError, 'provenance mismatch'):
                module.play(TerminalGame())
            source = policy.read_text()
            self.assertIn(c._sha(c.__file__), source)
            self.assertIn(c._sha(Path(c.__file__).with_name('ppo.py')), source)
            # Tamper the expected source digest in this temporary policy, never source itself.
            policy.write_text(source.replace(c._sha(c.__file__), '0'*64))
            other = importlib.util.module_from_spec(spec)
            exec(compile(policy.read_bytes(), str(policy), 'exec'), other.__dict__)
            with self.assertRaisesRegex(RuntimeError, 'provenance mismatch'):
                other.play(TerminalGame())


if __name__ == '__main__':
    unittest.main()
