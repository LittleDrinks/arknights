"""Offline public-API contracts, artificial states only; no game outcomes implied."""
import copy
import unittest
from experiments.sol.agent import Policy


class PublicCatalog:
    def lookup(self, q):
        data = {
            'chess': [
                {'id': 'front', 'name': 'Front', 'position': 'MELEE', 'profession': 'TANK', 'bonds': ['yanShip'], 'garrisonIds': []},
                {'id': 'back', 'name': 'Back', 'position': 'RANGED', 'profession': 'CASTER', 'bonds': ['yanShip', 'arcaneShip'], 'garrisonIds': ['on_refresh']},
            ],
            'bonds': [{'id': 'yanShip', 'name': '炎'}, {'id': 'arcaneShip', 'name': '奥术'}],
            'garrisons': [{'id': 'on_refresh', 'desc': '<休整期结束时>本回合每刷新过1次，使已激活的【奥术】层数+2（至多6层）'}],
        }
        entries = data.get(q['kind'], [])
        return {'entries': [e for e in entries if q.get('id', e['id']) == e['id']]}


class Contracts(unittest.TestCase):
    def policy(self, stacking=True):
        p = Policy(PublicCatalog(), stacking)
        p.banned, p.main, p.addons = set(), 'yanShip', ('arcaneShip',)
        p.mode, p.round, p.panic, p.boss_text, p.pub = 'engine', 6, False, '', {}
        return p

    def state(self):
        return {'actions': [{'t': 'g.move', 'targets': {'board': {'rows': [9, 12], 'cols': [2, 10]}}}],
                'own': {'board': [], 'hand': [{'kind': 'chess', 'id': 'front', 'uid': 1, 'tier': 1, 'items': []},
                                            {'kind': 'chess', 'id': 'back', 'uid': 2, 'tier': 1, 'items': []}, None],
                        'temp': [], 'deployCap': 8, 'deployCount': 0, 'nextEnemies': [],
                        'deployment': {'field': 'normal', 'cells': [{'row': 12, 'col': 2, 'kind': 'melee'},
                                                                   {'row': 9, 'col': 10, 'kind': 'ranged'}]}}}

    def test_placement_obeys_sparse_visible_terrain(self):
        p, state = self.policy(), self.state()
        a = p.arrange(state)
        piece = next(q for q in state['own']['hand'] if q and q['uid'] == a['uid'])
        kind = 'melee' if p.role(piece)[1] == 'MELEE' else 'ranged'
        self.assertIn((a['to']['row'], a['to']['col']), [(c['row'], c['col']) for c in state['own']['deployment']['cells']])
        self.assertEqual(a['dir'], 'UP')

    def test_missing_geometry_fails_explicitly(self):
        p, state = self.policy(), self.state()
        del state['own']['deployment']
        with self.assertRaisesRegex(RuntimeError, 'public own.deployment'):
            p.arrange(state)

    def test_engine_requires_linked_public_trigger(self):
        p, state = self.policy(), self.state()
        self.assertEqual(p.engine(state['own']['hand'][1]), ('refresh', ('arcaneShip',)))
        self.assertEqual(p.engine(state['own']['hand'][0]), ('', ()))
        p.banned.add('arcaneShip')
        p.engines.clear()
        self.assertEqual(p.engine(state['own']['hand'][1]), ('refresh', ()))

    def test_compact_attached_equipment_needs_no_kind(self):
        self.assertEqual(self.policy().meta({'id': 'chess_item_fake', 'uid': 45}), {})

    def test_ranged_candidates_include_visible_ground_cells(self):
        p, state = self.policy(), self.state()
        state['own']['hand'][0] = None
        state['own']['deployment']['cells'] = [{'row': 11, 'col': 4, 'kind': 'melee'}]
        a = p.arrange(state)
        self.assertEqual(a['to'], {'area': 'board', 'row': 11, 'col': 4})

    def test_ablation_preserves_terrain_and_api(self):
        state = self.state()
        # With no engine-bearing candidate, the flag cannot change ordinary placement.
        state['own']['hand'][1] = None
        self.assertEqual(self.policy(True).arrange(copy.deepcopy(state)),
                         self.policy(False).arrange(copy.deepcopy(state)))


if __name__ == '__main__':
    unittest.main()
