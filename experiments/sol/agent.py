#!/usr/bin/env python3
"""Independent Codex gpt-6.1-sol heuristic, pinned player interface only.

No runtime files, engine imports, seed access, native policy, or stat blackboards.
Engine hypotheses come from the cited cards and are gated by public card text.
Requires additive visible deployment cells and lookup bonds/garrisonIds metadata.
A small roster search values a fighting core, complementary supports, and roles.
"""
from __future__ import annotations

import itertools
import json
import re
from collections import Counter

AUTHOR = 'Codex gpt-6.1-sol'
CORE = ('yanShip', 'siracusaShip', 'kjeragShip', 'kazimierzShip',
        'victoriaShip', 'sargonShip', 'lateranoShip', 'egirShip')
PAIRS = {
    'yanShip': ('arcaneShip', 'skillfulShip', 'deputShip', 'visiShip'),
    'siracusaShip': ('arcaneShip', 'skillfulShip', 'deputShip', 'miraShip'),
    'kjeragShip': ('skillfulShip', 'arcaneShip', 'deputShip', 'steadShip'),
    'kazimierzShip': ('raidShip', 'preciShip', 'indomShip', 'deputShip'),
    'victoriaShip': ('skillfulShip', 'deputShip', 'steadShip', 'swiftShip'),
    'sargonShip': ('preciShip', 'swiftShip', 'skillfulShip', 'deputShip'),
    'lateranoShip': ('skillfulShip', 'preciShip', 'visiShip', 'deputShip'),
    'egirShip': ('arcaneShip', 'indomShip', 'steadShip', 'deputShip'),
}
GUIDES = {
    'defend': 'BV1MZbx6hEm8:R01', 'survive': 'BV1M5Q7BtEYE:R02',
    'refresh': 'BV1agw6zREss:R01', 'acquire': 'BV1kgwjzqExY:R01',
    'copy': 'BV17rD1BSE2u:R02', 'deployment': 'BV1g9AGz1EbT:R02',
    'support': 'BV1nyDKB5Eoj:R02', 'boss': 'BV19RYp6zEry:R01',
}


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def pieces(own):
    return [p for area in ('board', 'hand', 'temp') for p in own.get(area, []) if p]


def bond_id(b):
    return b.get('id', b.get('bondId', ''))


def bond_count(b):
    for key in ('count', 'num', 'n', 'memberCount'):
        if isinstance(b.get(key), (int, float)):
            return b[key]
    for key in ('members', 'chessIds', 'units'):
        if isinstance(b.get(key), list):
            return len(b[key])
    return 0


class Policy:
    def __init__(self, game, stacking=True):
        self.game, self.stacking = game, stacking
        self.cache, self.bad_tiles = {}, set()
        self.main, self.addons, self.round = None, (), None
        self.rejected, self.refreshes, self.buys, self.actions = set(), 0, 0, 0
        self.notes, self.engine_spent, self.last_lp = [], 0, None
        self.known_tiles, self.round_plans = {}, {}
        self.values, self.lineups, self.engines, self.garrison_cache = {}, {}, {}, {}

    def lookup(self, kind, id=None, text=None):
        key = (kind, id, text)
        if key not in self.cache:
            q = {'kind': kind, 'limit': 100}
            if id is not None:
                q['id'] = id
            if text is not None:
                q['text'] = text
            self.cache[key] = self.game.lookup(q).get('entries', [])
        return self.cache[key]

    def meta(self, p):
        kind = 'chess' if p.get('kind', 'item' if p['id'].startswith('chess_item_') else 'chess') == 'chess' else 'items'
        es = self.lookup(kind, p['id'])
        return es[0] if es else {}

    def name(self, p):
        if p.get('uid') == getattr(self, 'pith_uid', None):
            return 'Pith (public band grant)'
        return self.meta(p).get('name', p.get('name', p['id']))

    def labels(self, p):
        meta = self.meta(p)
        # The additive public API makes these authoritative, including empty lists.
        if 'bonds' in meta:
            return set(meta['bonds']) - self.banned
        if p.get('uid') == getattr(self, 'pith_uid', None):
            return {'maniShip'}
        return set()

    def garrison_text(self, p):
        if p['id'] in self.garrison_cache:
            return self.garrison_cache[p['id']]
        descriptions = []
        for gid in self.meta(p).get('garrisonIds', []):
            descriptions.extend(e.get('desc', '') for e in self.lookup('garrisons', gid))
        self.garrison_cache[p['id']] = '\n'.join(descriptions)
        return self.garrison_cache[p['id']]

    def role(self, p):
        m = self.meta(p)
        if p.get('uid') == getattr(self, 'pith_uid', None):
            return 'CASTER', 'RANGED'
        return m.get('profession', ''), m.get('position', 'RANGED')

    def active(self, own):
        return {bond_id(b) for b in own.get('bonds', []) if b.get('active', False)}

    def engine(self, p):
        key = (p['id'], self.main, self.addons)
        if key in self.engines:
            return self.engines[key]
        text = self.garrison_text(p)
        bond_names = {e['name']: e['id'] for e in self.lookup('bonds')}
        feeds = {bid for name, bid in bond_names.items() if '【' + name + '】' in text}
        if '自身' in text:
            feeds |= self.labels(p)
        if '已激活盟约' in text or '当前所有' in text:
            feeds |= {self.main} | set(self.addons)
        kind = ''
        if '每刷新过' in text and '至多' in text:
            kind = 'refresh'
        elif '首次主动刷新' in text:
            kind = 'first_refresh'
        elif '每获得过' in text:
            kind = 'acquire'
        elif '身前' in text and '进入休整' in text:
            kind = 'copy'
        elif '同一行最右' in text:
            kind = 'row_right'
        elif '不同阶' in text:
            kind = 'tiers'
        elif '进入休整期' in text or '休整期结束' in text:
            kind = 'prep'
        elif '部署时' in text:
            kind = 'deployment'
        elif '层数+' in text:
            kind = 'combat'
        self.engines[key] = (kind, tuple(sorted(feeds - self.banned)))
        return self.engines[key]

    def transition(self, state):
        own, pub = state['own'], state['public']
        self.banned = set(pub.get('disabledBonds', []))
        self.boss = pub.get('bossId')
        self.boss_text = compact(self.lookup('bosses', self.boss)) if self.boss else ''
        if state['round'] == 1 and own.get('bandId') == 'band_pith' and not hasattr(self, 'pith_uid'):
            unknown = [p for p in pieces(own) if p['kind'] == 'chess' and not self.meta(p)]
            if len(unknown) == 1:
                self.pith_uid = unknown[0]['uid']
        if state['round'] != self.round:
            self.round = state['round']
            self.rejected, self.refreshes, self.buys, self.actions = set(), 0, 0, 0
            self.notes, self.engine_spent = [], 0
            self.hurt = self.last_lp is not None and own['lp'] < self.last_lp
            self.last_lp = own['lp']
            self.panic = own['lp'] <= 10 or self.hurt
            self.mode = 'survival' if self.panic else ('boss' if self.round >= pub['bossRound'] - 2
                        else 'bridge' if self.round < 5 else 'engine')
            if self.main is None:
                self.main = next((b for b in CORE if b not in self.banned), None)
            self.choose_core(own)
            self.values, self.lineups = {}, {}
            self.notes.append({'transition': self.mode, 'cause': 'LP loss/low LP' if self.panic
                               else 'round schedule; no teammate strength assumed'})

    def choose_core(self, own):
        roster = [p for p in pieces(own) if p['kind'] == 'chess']
        scores = {}
        for b in CORE:
            if b in self.banned:
                continue
            matching = [p for p in roster if b in self.labels(p)]
            score = sum(1 + .2 * p.get('tier', 1) + .8 * p.get('golden', False) for p in matching)
            score += 1 if b == self.main else 0
            if b == 'egirShip':
                score -= 1  # avoid destroying scarce early defense with unplanned feeding
            if b in ('yanShip', 'kazimierzShip') and '闪避' in self.boss_text:
                score += .6
            scores[b] = score
        current_count = len({p['id'] for p in roster if self.main in self.labels(p)})
        if scores and (self.round <= 6 or current_count < 3):
            candidate = max(scores, key=lambda b: (scores[b], -CORE.index(b)))
            if candidate != self.main and scores[candidate] >= scores.get(self.main, 0) + 1.5:
                self.notes.append({'core_change': [self.main, candidate], 'reason': 'observed roster fit'})
                self.main = candidate
        self.addons = tuple(b for b in PAIRS.get(self.main, ()) if b not in self.banned)[:3]

    def value(self, p, own):
        if p['kind'] != 'chess':
            return self.item_value(p, own)
        key = (p['id'], p.get('tier', p.get('basePrice', p.get('price', 1))), p.get('golden', False), p.get('uid') == getattr(self, 'pith_uid', None))
        if key in self.values:
            return self.values[key]
        meta, name = self.meta(p), self.name(p)
        prof, pos = self.role(p)
        labs = self.labels(p)
        score = 3 + 1.25 * p.get('tier', p.get('basePrice', p.get('price', 1))) + 3.5 * p.get('golden', p['id'].endswith('_b'))
        score += 3.8 * (self.main in labs) + 1.3 * len(set(self.addons) & labs)
        if prof in ('WARRIOR', 'TANK'):
            score += 1.4
        if prof == 'MEDIC':
            score += 1
        if prof == 'SNIPER' and ('优先攻击空中' in compact(meta) or any(e.get('fly') for e in own['nextEnemies'])):
            score += 2
        if '隐匿' in compact(own.get('nextEnemies', [])) and '隐匿' in compact(meta):
            score += 2
        if 'INVISIBLE' in self.pub.get('factions', []) and name == '伊内丝':
            score += 4
        if '真实伤害' in compact(meta.get('skill', {})) or '元素' in compact(meta.get('skill', {})):
            score += 1.5 if self.mode == 'boss' else .5
        kind, feeds = self.engine(p)
        if self.stacking and set(feeds) & ({self.main} | set(self.addons)):
            score += (3 if self.mode == 'engine' else 1.4) if kind else 0
        if name in ('白面鸮', '浊心斯卡蒂', '黍', '铃兰'):
            score += 1.5
        if self.mode == 'boss' and p.get('tier', 1) <= 2 and not p.get('golden'):
            score -= 1.5
        self.values[key] = score
        return score

    def team_value(self, lineup, own):
        score = sum(self.value(p, own) for p in lineup)
        counts = Counter()
        seen = set()
        for p in lineup:
            base = self.meta(p).get('baseId', re.sub('_b$', '_a', p['id']))
            if base not in seen:
                counts.update(self.labels(p))
                seen.add(base)
        if counts['maniShip']:
            for b in CORE:
                if counts[b]:
                    counts[b] += 1
        main = counts[self.main]
        score += min(6, main) * 3 + (9 if main >= 3 else 0) + (18 if main >= 6 else 0)
        for b in self.addons:
            n = counts[b]
            score += 3 * min(n, 2) + (5 if n >= 3 else 0)
        # Preserve active survival/economy combinations even outside the preferred addons.
        for b in ('deputShip', 'steadShip', 'skillfulShip', 'arcaneShip', 'visiShip', 'miraShip'):
            if b not in self.banned and counts[b] >= 2:
                score += 4
        if counts['maniShip']:
            score += 4
        roles = Counter(self.role(p)[0] for p in lineup)
        score += 8 * min(roles['TANK'] + roles['WARRIOR'] + roles['PIONEER'], 2)
        score += 9 * min(roles['MEDIC'], 1)
        score -= 9 * max(0, roles['MEDIC'] - 1)
        score += 5 * min(roles['SNIPER'] + roles['CASTER'], 2)
        score -= 5 * max(0, roles['MEDIC'] - 2)
        # Distinct operators, rather than raw instances, count towards most bonds.
        score -= 3 * (len(lineup) - len({re.sub('_b$', '_a', p['id']) for p in lineup}))
        return score

    def lineup(self, own):
        key = (own['deployCap'], tuple(sorted((p['uid'], p['id'], len(p.get('items', []))) for p in pieces(own))))
        if key in self.lineups:
            return self.lineups[key]
        roster = [p for p in pieces(own) if p['kind'] == 'chess']
        cap = max(0, own['deployCap'] - sum(p['kind'] != 'chess' for p in own['board']))
        roster.sort(key=lambda p: (-self.value(p, own), p['uid']))
        # Enumerate small substitutions around a greedy base, bounded independently of inventory.
        if len(roster) <= cap:
            return roster
        candidates = roster[:min(len(roster), cap + 4)]
        best = max(itertools.combinations(candidates, cap), key=lambda team: self.team_value(team, own))
        self.lineups[key] = list(best)
        return self.lineups[key]

    def item_value(self, p, own):
        text = compact(self.meta(p))
        if not self.stacking and '层数+' in text and '装备时销毁' in text:
            return -10
        score = 2
        for word in ('攻击力', '攻击速度', '技力', '免疫', '复活', '回复自身', '弱点伤害'):
            score += 1.5 * (word in text)
        if '生命值-30%' in text or '流失' in text:
            score -= 5
        if '目标干员和本装备销毁' in text or '额外敌人' in text:
            score -= 15
        if self.main == 'kjeragShip' and '寒冷' in text:
            score += 5
        if '晋升为精锐' in text:
            score += 6
        if '每回合开始时额外获得' in text and self.round <= 9:
            score += 5
        return score

    def attempt(self, state, action):
        key = compact(action)
        if key in self.rejected:
            return state, False
        uid = action.get('uid')
        piece = next((p for p in pieces(state['own']) if p['uid'] == uid), None)
        reply = self.game.act(action)
        self.actions += 1
        after = reply['state']
        if not reply['accepted']:
            self.rejected.add(key)
            self.notes.append({'reject': action['type'], 'error': reply.get('result', {}).get('error')})
            if action['type'] == 'move' and action.get('to', {}).get('area') == 'board' and piece:
                error = compact(reply.get('result', {})).lower()
                if any(w in error for w in ('tile', 'terrain', 'position', 'cell')):
                    self.bad_tiles.add((self.role(piece)[1], action['to']['row'], action['to']['col']))
            return after, False
        if action['type'] == 'refresh':
            self.refreshes += 1
        if action['type'] == 'buy':
            self.buys += 1
        if piece and action['type'] == 'move' and action['to']['area'] == 'board':
            self.known_tiles[(self.role(piece)[1], action['to']['row'], action['to']['col'])] = True
        return after, True

    def allowed(self, action):
        return compact(action) not in self.rejected

    def arrange(self, state):
        own = state['own']
        wanted = self.lineup(own)
        wanted_ids = {p['uid'] for p in wanted}
        for p in own['board']:
            if p['kind'] == 'chess' and p['uid'] not in wanted_ids:
                if None in own['hand']:
                    a = {'type': 'move', 'uid': p['uid'], 'to': {'area': 'hand', 'idx': own['hand'].index(None)}}
                    if self.allowed(a):
                        return a
        ops = {a['t']: a for a in state['actions']}
        domain = ops.get('g.move', {}).get('targets', {}).get('board')
        if not domain:
            return None
        r0, r1 = domain['rows']; c0, c1 = domain['cols']
        occupied = {(p['row'], p['col']) for p in own['board']}
        board_ids = {p['uid'] for p in own['board']}
        for p in sorted(wanted, key=lambda p: (-self.value(p, own), p['uid'])):
            if p['uid'] in board_ids or own['deployCount'] >= own['deployCap']:
                continue
            melee = self.role(p)[1] == 'MELEE'
            # Face the enemy half of the field. Defenders screen ranged/healing units.
            row_target = r0 if melee else r0 + 2
            used_cols = [q['col'] for q in own['board'] if q['kind'] == 'chess']
            col_target = (c0 + c1) / 2
            deployment = own.get('deployment')
            if deployment is None:
                raise RuntimeError('SOL requires public own.deployment terrain; no probing fallback')
            wanted_kind = 'melee' if melee else 'ranged'
            # Terrain is visible geometry, not a class-specific legality mask. The native
            # validator permits ranged operators on these autochess ground cells too.
            tiles = [(c['row'], c['col']) for c in deployment['cells']]
            def tile_score(tile):
                r, c = tile
                density = sum(max(0, 2 - abs(c - old)) for old in used_cols)
                relation = 0
                if 'skillfulShip' in self.labels(p):
                    relation += min((abs(r - q['row']) + abs(c - q['col']) for q in own['board']), default=0)
                if self.stacking:
                    for q in own['board']:
                        if q['kind'] != 'chess':
                            continue
                        if self.name(p) == '白面鸮' and self.name(q) == '空弦':
                            relation += 12 * abs(abs(r - q['row']) + abs(c - q['col']) - 1)
                        if self.name(p) == '远牙' and self.name(q) == '瑕光':
                            relation += 10 * abs(r - q['row']) + 8 * (c >= q['col'])
                        if self.name(p) == '瑕光' and self.name(q) == '远牙':
                            relation += 10 * abs(r - q['row']) + 8 * (c <= q['col'])
                return (relation + abs(r - row_target) * 3 + abs(c - col_target) + density * .5
                        - .3 * self.known_tiles.get((self.role(p)[1], r, c), False))
            for row, col in sorted(tiles, key=tile_score):
                if (row, col) in occupied or (self.role(p)[1], row, col) in self.bad_tiles:
                    continue
                a = {'type': 'move', 'uid': p['uid'], 'to': {'area': 'board', 'row': row, 'col': col}, 'dir': 'UP'}
                if self.allowed(a):
                    return a
        # Copy prep-entry effects with the precise adjacent relation. Reorient rather than displacing a defense.
        if self.stacking:
            for p in own['board']:
                if p['kind'] != 'chess' or self.name(p) != '白面鸮':
                    continue
                for q in own['board']:
                    if q['kind'] != 'chess' or self.name(q) != '空弦':
                        continue
                    delta = (q['row'] - p['row'], q['col'] - p['col'])
                    direction = {(-1, 0): 'UP', (1, 0): 'DOWN', (0, -1): 'LEFT', (0, 1): 'RIGHT'}.get(delta)
                    if direction and p.get('dir') != direction:
                        a = {'type': 'move', 'uid': p['uid'], 'to': {'area': 'board', 'row': p['row'], 'col': p['col']}, 'dir': direction}
                        if self.allowed(a):
                            return a
        return None

    def equip(self, own):
        loose = [p for p in own['hand'] + own['temp'] if p and p['kind'] == 'item']
        carriers = [p for p in own['board'] if p['kind'] == 'chess']
        for item in sorted(loose, key=lambda p: -self.item_value(p, own)):
            text = compact(self.meta(item))
            if '拖拽至场上使用' in text:
                # Only safe cloning is deliberately used; do not add enemies during survival.
                if '画卷' in text and carriers and (self.round >= 7 or self.panic):
                    q = max(carriers, key=lambda p: self.value(p, own))
                    a = {'type': 'art', 'itemUid': item['uid'], 'row': q['row'], 'col': q['col'], 'dir': 'UP'}
                    if self.allowed(a):
                        return a
                continue
            if not self.stacking and '层数+' in text and '装备时销毁' in text:
                continue
            if self.item_value(item, own) < 0:
                continue
            def utility(p):
                prof, pos = self.role(p)
                score = self.value(p, own)
                if '治疗' in text:
                    score += 25 if prof == 'MEDIC' else -30
                if any(w in text for w in ('防御力', '生命值', '复活', '减免')):
                    score += 7 if pos == 'MELEE' else 0
                if '法术' in text:
                    score += 7 if prof == 'CASTER' else 0
                if '弹药' in text or '子弹' in text:
                    score += 7 if '弹药' in compact(self.meta(p)) else -5
                if self.main == 'victoriaShip' and 'victoriaShip' in self.labels(p) and not p['items']:
                    score += 7
                if '晋升为精锐' in text and p.get('golden'):
                    score -= 100
                return score
            for p in sorted(carriers, key=lambda p: (-utility(p), p['uid'])):
                if len(p['items']) >= 2:
                    # Permit money and promotion consumables through the native validator.
                    if '装备时销毁' not in text and '晋升' not in text:
                        continue
                a = {'type': 'equip', 'itemUid': item['uid'], 'targetUid': p['uid']}
                if self.allowed(a):
                    return a
        return None

    def choice(self, state):
        own, pub = state['own'], state['public']
        descriptor = next(a for a in state['actions'] if a['t'] == 'g.choice')
        domain = descriptor['targets']['idx']
        personal = own.get('personalChoice')
        cards = personal['cards'] if personal else pub.get('sp', {}).get('cards', [])
        def score(i):
            c = cards[i]
            text = compact(c)
            n = 0
            for bid in (self.main,) + self.addons:
                names = self.lookup('bonds', bid)
                if bid in text or names and names[0].get('name', 'UNMATCHED') in text:
                    n += 4
            for term in ('攻击', '生命', '技力', '回复', '资金', '免费', '精锐'):
                n += term in text
            for term in ('损失', '扣除', '额外敌人'):
                n -= 5 * (term in text)
            if c.get('kind') in ('chess', 'item') and c.get('id'):
                n += self.value(c, own)
            return n
        idx = max(domain, key=lambda i: (score(i), -i))
        self.notes.append({'choice': idx, 'card': cards[idx]})
        a = {'type': 'choice', 'idx': idx}
        if personal:
            a['choiceId'] = personal['id']
        return a

    def cleanup(self, own, force=False):
        wanted = {p['uid'] for p in self.lineup(own)}
        temp = [p for p in own['temp'] if p]
        for p in temp:
            if None in own['hand']:
                a = {'type': 'move', 'uid': p['uid'], 'to': {'area': 'hand', 'idx': own['hand'].index(None)}}
                if self.allowed(a):
                    return a
        if temp or force:
            unwanted = [p for p in pieces(own) if p['uid'] not in wanted and p not in own['board']]
            unwanted.sort(key=lambda p: (self.value(p, own), p['uid']))
            for p in unwanted:
                kind = 'sell' if p['kind'] == 'chess' else 'destroy' if p['kind'] == 'item' else None
                if kind:
                    a = {'type': kind, 'uid': p['uid']}
                    if self.allowed(a):
                        self.notes.append({'overflow_resolution': kind, 'uid': p['uid']})
                        return a
        return None

    def shopping(self, state):
        own, shop = state['own'], state['own']['shop']
        roster = [p for p in pieces(own) if p['kind'] == 'chess']
        counts = Counter(re.sub('_b$', '_a', p['id']) for p in roster if not p.get('golden'))
        board = [p for p in own['board'] if p['kind'] == 'chess']
        target_level = min(6, 1 + self.round // 2)
        # Buy a defense before investing into access to higher tiers.
        if shop['level'] < target_level and own['funds'] >= shop['upgradePrice'] and len(board) >= min(4, self.round + 1):
            a = {'type': 'levelUp'}
            if self.allowed(a):
                self.notes.append({'economy': 'upgrade for higher-tier damage/support access', 'price': shop['upgradePrice']})
                return a
        if self.buys < 8 and None in own['hand']:
            options = []
            for i, s in enumerate(shop['slots']):
                if not s or s.get('sold') or s['price'] > own['funds']:
                    continue
                a = {'type': 'buy', 'slot': i}
                if not self.allowed(a):
                    continue
                score = self.value(s, own)
                if s['kind'] == 'chess':
                    base = re.sub('_b$', '_a', s['id'])
                    score += 9 if counts[base] >= 2 else 3.5 if counts[base] == 1 else 0
                    if len(roster) >= own['deployCap']:
                        # Score the best one-for-one improvement, including lost bond thresholds.
                        current = self.lineup(own)
                        candidate = dict(s, uid=-100-i, tier=s.get('basePrice', s['price']), golden=False, items=[])
                        gain = max((self.team_value([q for q in current if q['uid'] != old['uid']] + [candidate], own)
                                    - self.team_value(current, own) for old in current), default=score)
                        score = gain + (8 if counts[base] >= 2 else 3 if counts[base] == 1 else 0)
                    if len(roster) >= own['deployCap'] + 5 and counts[base] < 2:
                        score -= 10
                    roles = [self.role(p)[0] for p in board]
                    prof, _ = self.role(s)
                    if prof == 'MEDIC' and 'MEDIC' not in roles and self.round >= 3:
                        score += 5
                    if prof in ('TANK', 'WARRIOR') and not any(r in ('TANK', 'WARRIOR') for r in roles):
                        score += 7
                else:
                    if len(board) < 3:
                        continue
                    score += 3 if any(len(p['items']) < 2 for p in board) else -10
                score -= .5 * s['price']
                if score > 1:
                    options.append((score, -i, a))
            if options:
                return max(options, key=lambda x: x[:2])[2]
        # Refreshes must either search an improving roster, or operate a bounded, active engine.
        active = self.active(own)
        engines = [(self.engine(p), self.name(p)) for p in board]
        feeds = [(kind, name) for ((kind, bonds), name) in engines
                 if set(bonds) & active & ({self.main} | set(self.addons))]
        engine_goal = 0
        if self.stacking and not self.panic and self.mode != 'boss':
            if any(kind == 'refresh' for kind, _ in feeds):
                engine_goal = 3
            elif any(kind == 'first_refresh' for kind, _ in feeds):
                engine_goal = 1
        # Never assume miracle is free: the displayed refreshPrice is authoritative.
        reserve = 0 if self.round >= 11 or self.panic else 2
        can_search = self.refreshes < (5 if self.panic else 4) and self.buys < 8
        if own['funds'] >= shop['refreshPrice'] + reserve and (can_search or self.refreshes < engine_goal):
            a = {'type': 'refresh'}
            if self.allowed(a):
                if self.refreshes < engine_goal:
                    self.notes.append({'engine': 'bounded first/three refresh', 'tools': feeds,
                                       'visible_price': shop['refreshPrice'], 'evidence': GUIDES['refresh']})
                return a
        # Deliberate acquisition cycling is safe only with an active feeder and a visible cheap card.
        # Preserve all existing identities, pairs and equipped units. Never sell an ambiguous merged identity.
        if self.stacking and self.mode == 'engine' and self.engine_spent < 3 and None in own['hand']:
            feeders = [name for kind, name in feeds if kind == 'acquire']
            if feeders:
                for i, s in enumerate(shop['slots']):
                    if s and s['kind'] == 'chess' and not s.get('sold') and s['price'] <= 2 and own['funds'] >= s['price'] + 2:
                        if not any(p['id'] == s['id'] for p in roster) and self.buys < 8:
                            a = {'type': 'buy', 'slot': i}
                            if self.allowed(a):
                                self.cycle_id = s['id']
                                self.engine_spent += s['price']
                                self.notes.append({'engine': 'acquire then sell unique new hand card', 'feeders': feeders,
                                                   'evidence': GUIDES['acquire']})
                                return a
        return None

    def report(self, state, event='round'):
        own = state['own']
        roster = [p for p in own['board'] if p['kind'] == 'chess']
        print(compact({'author': AUTHOR, 'event': event, 'round': state['round'], 'mode': self.mode,
                       'main': self.main, 'addons': self.addons, 'stacking': self.stacking,
                       'LP': own['lp'], 'funds': own['funds'], 'teamLP': state['public'].get('teamLp'),
                       'bonds': own.get('bonds', []), 'level': own['shop']['level'],
                       'lineup': [{'uid': p['uid'], 'name': self.name(p), 'tier': p.get('tier'),
                                   'elite': p.get('golden'), 'row': p['row'], 'col': p['col'],
                                   'dir': p.get('dir'), 'items': [self.name(i) for i in p['items']]} for p in roster],
                       'engine_candidates': [{'name': self.name(p), 'trigger': self.engine(p)[0], 'public_text': self.garrison_text(p)}
                                             for p in roster if self.engine(p)[0]],
                       'actual_cumulative_stats': own.get('stats'), 'refreshes_this_prep': self.refreshes,
                       'decisions': self.notes[-16:], 'evidence': GUIDES,
                       'opening_bans': sorted(self.banned), 'boss': self.boss,
                       'note': 'Guide priors; displayed bonds/layers are observations, not projected gains.'}), flush=True)

    def prep(self, state):
        own = state['own']
        if self.actions < 140:
            if getattr(self, 'cycle_id', None):
                matches = [p for p in own['hand'] if p and p['kind'] == 'chess' and p['id'] == self.cycle_id]
                self.cycle_id = None
                if len(matches) == 1 and not matches[0]['items'] and not matches[0].get('golden'):
                    return {'type': 'sell', 'uid': matches[0]['uid']}
                self.notes.append({'engine_stop': 'acquisition identity ambiguous; preserve cards'})
            a = self.arrange(state)
            if a:
                return a
            a = self.equip(own)
            if a:
                return a
            offer = own['shop'].get('rewardOffer')
            if offer and None in own['hand']:
                candidates = [(self.value(s, own), -i, i) for i, s in enumerate(offer['slots'])
                              if s and not s.get('sold') and s.get('price', 0) <= own['funds']
                              and self.allowed({'type': 'reward', 'idx': i})]
                if candidates:
                    return {'type': 'reward', 'idx': max(candidates)[2]}
            a = self.cleanup(own)
            if a:
                return a
            if None not in own['hand']:
                a = self.cleanup(own, force=True)
                if a:
                    return a
            a = self.shopping(state)
            if a:
                return a
        if not own.get('canReady'):
            a = self.cleanup(own, force=True)
            if a and self.actions < 180:
                return a
            self.report(state, 'unresolved-preparation')
            raise RuntimeError('SOL bounded preparation cannot legally resolve overflow; no fake ready/win')
        self.report(state)
        return {'type': 'ready', 'ready': True}

    def run(self):
        state = self.game.observe()
        waits = 0
        while not state['terminal']:
            self.pub = state['public']
            self.transition(state)
            if not state['can_act']:
                nxt = self.game.wait()
                waits = waits + 1 if nxt == state else 0
                if waits > 5:
                    raise RuntimeError('SOL wait made no progress; refusing to fabricate completion')
                state = nxt
                continue
            waits = 0
            ops = {a['t']: a for a in state['actions']}
            if state['phase'] == 'INFO_CHECK':
                print(compact({'event': 'opening', 'bans': sorted(self.banned), 'boss': self.boss,
                               'boss_public_text': self.boss_text, 'initial_main': self.main, 'addons': self.addons}), flush=True)
                action = {'type': 'confirm', 'setupRevision': self.pub['setupRevision']}
            elif 'g.band' in ops:
                ids = ops['g.band']['targets']['bandId']
                # A free elemental harmony unit supports the six-core breakpoint without a speculative economy.
                priority = ['band_pith', 'band_amiya', 'band_ioleta', 'band_chen', 'band_ermengard']
                if '降低' in self.boss_text and '元素' not in self.boss_text:
                    priority = ['band_pith', 'band_chen', 'band_amiya', 'band_ioleta', 'band_ermengard']
                choice = next((b for b in priority if b in ids), ids[0])
                self.lookup('bands', choice)
                action = {'type': 'band', 'bandId': choice}
            elif 'g.choice' in ops:
                action = self.choice(state)
            elif state['phase'] == 'PREP':
                action = self.prep(state)
            else:
                raise RuntimeError('SOL unknown live decision phase: ' + state['phase'])
            state, ok = self.attempt(state, action)
            if not ok and action['type'] in ('confirm', 'band', 'choice', 'ready'):
                raise RuntimeError('SOL mandatory decision rejected: ' + compact(action))
        self.report(state, 'terminal')
        print(compact({'event': 'outcome', 'outcome': state.get('outcome')}), flush=True)


def play(game):
    Policy(game, stacking=True).run()
