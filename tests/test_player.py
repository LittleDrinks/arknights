from copy import deepcopy
import unittest

from arknights.adapter import Session
from experiments.heuristic import play


class PlayerTests(unittest.TestCase):
    def test_adapter_execution_error_is_not_an_ordinary_rejected_move(self):
        class Child:
            def request(self, _):
                return {'ok': True, 'accepted': False,
                        'result': {'error': 'ADAPTER_ERROR', 'detail': 'engine callback failed'}}
        session = Session()
        session._child = Child()
        with self.assertRaisesRegex(RuntimeError, 'engine callback failed'):
            session.act({'type': 'buy', 'slot': 0})

    def test_heuristic_resolves_temporary_items_and_tokens_without_waiting(self):
        for kind, expected in [('item', 'destroy'), ('token', 'move'), ('chess', 'sell')]:
            with self.subTest(kind=kind):
                class Game:
                    def __init__(self):
                        self.actions = []
                        self.state = {'terminal': False, 'round': 1, 'phase': 'PREP', 'can_act': True,
                            'public': {}, 'actions': [], 'own': {'board': [], 'hand': [None],
                            'temp': [{'uid': 4, 'kind': kind}], 'deployCount': 0, 'deployCap': 0,
                            'funds': 0, 'shop': {'slots': []}, 'canReady': False}}
                    def observe(self):
                        return deepcopy(self.state)
                    def act(self, action):
                        self.actions.append(action)
                        if action['type'] == 'ready':
                            self.state['terminal'] = True
                        else:
                            self.state['own']['temp'] = []
                            self.state['own']['canReady'] = True
                        return {'state': self.observe()}
                    def wait(self):
                        raise AssertionError('Waiting does not resolve untimed overflow')
                game = Game()
                play(game)
                self.assertEqual([a['type'] for a in game.actions], [expected, 'ready'])


if __name__ == '__main__':
    unittest.main()
