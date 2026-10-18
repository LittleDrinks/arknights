"""Shared CPU candidate-scoring environment. No legacy controller or legal-action oracle.

Shortlist: at most 64 draft/choice/buy/reward/sell targets, 16 move sources,
8 evenly spaced visible deployment tiles (board-domain fallback on old adapters) plus each source's current tile, all published
orientations, first 2 empty hand slots; 8 item x 8 carrier equip combinations
(with each carrier's replacement items), 4 Art items x 8 tiles x orientations.
These are parameter-domain candidates, not guaranteed legal combinations. Rejected
candidates are masked only for an identical visible-state fingerprint. No penalty.
Wait is offered only without a substantive player decision. Empty actionable
shortlists fail explicitly rather than allowing the engine to choose for us.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import time
import uuid

import torch
from torch import nn
from arknights.adapter import RULES, Session

OBS_DIM = 96
ACTION_DIM = 96
TYPES = ('confirm', 'band', 'bandSkip', 'choice', 'buy', 'reward', 'refresh',
         'freeze', 'levelUp', 'sell', 'move', 'equip', 'art', 'destroy', 'ready', 'wait')
PHASES = ('INFO_CHECK', 'BAND_DRAFT', 'ROUND_START', 'SP_DRAFT', 'PREP', 'COMBAT',
          'UNITE', 'FINAL_ASSAULT', 'HIDDEN_CORE', 'RESULT')
SHORTLIST = __doc__.strip()
# Public named CORE categories only (game/docs/research/02-bonds.md overview).
# No activation thresholds, numerical stats, or hidden table values are encoded.
CORE_BONDS = frozenset(('yanShip', 'sargonShip', 'victoriaShip', 'kjeragShip',
                        'lateranoShip', 'egirShip', 'siracusaShip', 'kazimierzShip'))


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def action_key(action):
    return _json(action)


def fingerprint(state):
    return hashlib.sha256(_json(state).encode()).hexdigest()


def _number(value, scale=1):
    if not isinstance(value, (float, int)) or not math.isfinite(value):
        return 0.
    return math.tanh(float(value) / scale)


def _hash_features(value, size):
    """Stable signed text bags; public identities/text only, no Python hash seed."""
    out = [0.] * size
    for token in re.findall(r'[\w]+', _json(value).lower()):
        h = hashlib.blake2b(token.encode(), digest_size=8).digest()
        out[int.from_bytes(h[:4], 'little') % size] += 1 if h[4] & 1 else -1
    norm = max(1., math.sqrt(sum(x*x for x in out)))
    return [x / norm for x in out]


def _pieces(own):
    return [p for area in ('board', 'hand', 'temp') for p in own.get(area, []) if p]


def _core_layers(state):
    return max([0.] + [float(b.get('layers', 0)) for b in state.get('own', {}).get('bonds', [])
                      if b.get('active') and not b.get('off') and b.get('bondId') in CORE_BONDS])


def potential(state):
    """Bounded [0,.2] shaping: round saturation + max activated core layers."""
    if state.get('terminal'):
        return 0.
    return .1 * min(max(float(state.get('round', 0)), 0.) / 20, 1.) + .1 * math.tanh(_core_layers(state) / 100)


@dataclass
class Decision:
    obs: list[float]
    action_features: list[list[float]]
    actions: list[dict]
    terminal: bool


def _tiles(targets, deployment=None):
    if deployment is not None:
        tiles = sorted({(cell['row'], cell['col']) for cell in deployment.get('cells', [])})
    else:
        board = targets.get('board', {})
        if not board:
            return []
        rows, cols = board['rows'], board['cols']
        tiles = [(r, c) for r in range(rows[0], rows[1]+1) for c in range(cols[0], cols[1]+1)]
    if len(tiles) <= 8:
        return tiles
    return [tiles[round(i*(len(tiles)-1)/7)] for i in range(8)]


def build_decision(state, references=None, blocked=None):
    own, public = state.get('own', {}), state.get('public', {})
    pieces = _pieces(own)
    shop = own.get('shop', {})
    numeric = [state.get('round', 0)/20, own.get('funds', 0)/30, own.get('lp', 0)/10,
               own.get('deployCount', 0)/10, own.get('deployCap', 0)/10,
               shop.get('level', 0)/10, shop.get('upgradePrice', 0)/30,
               shop.get('refreshPrice', 0)/10, len(own.get('board', []))/10,
               sum(p is not None for p in own.get('hand', []))/10,
               sum(p is not None for p in own.get('temp', []))/5,
               float(own.get('alive', False)), float(own.get('canReady', False)),
               float(state.get('can_act', False)), float(shop.get('frozen', False)),
               _core_layers(state)/100, public.get('teamLp', 0)/20,
               len(public.get('bannedChess', []))/20, len(public.get('disabledBonds', []))/20,
               float(own.get('ready', False)),
               *[float(state.get('phase') == phase) for phase in PHASES], 0., 0.]
    # No seed, RNG, hidden Match, or numeric data tables enter the network.
    visible = {'pieces': [{k: p[k] for k in ('id', 'kind', 'row', 'col', 'dir', 'golden', 'tier', 'items') if k in p}
                          for p in pieces], 'shop': shop, 'bonds': own.get('bonds', []),
               'choice': own.get('personalChoice'), 'opening': {k: public.get(k) for k in
                   ('bossId', 'bannedChess', 'disabledBonds', 'factions', 'stageId', 'sp')},
               'band': own.get('bandId'), 'effects': own.get('effects', []),
               'deployment': own.get('deployment')}
    obs = [_number(x) for x in numeric] + _hash_features(visible, 64)
    if len(obs) != OBS_DIM:
        raise AssertionError('Observation dimension changed')
    actions = []
    ops = {a['t']: a.get('targets', {}) for a in state.get('actions', [])}
    def add(kind, **params):
        actions.append({'type': kind, **params, **({'revision': state['revision']} if 'revision' in state else {})})
    if not state.get('terminal'):
        if not state.get('can_act'):
            add('wait')
        else:
            if 'g.infoReady' in ops:
                add('confirm', setupRevision=public.get('setupRevision', 0))
            for band in ops.get('g.band', {}).get('bandId', [])[:64]:
                add('band', bandId=band)
            if 'g.bandSkip' in ops:
                add('bandSkip')
            choice = ops.get('g.choice')
            if choice:
                for idx in choice.get('idx', [])[:64]:
                    params = {'idx': idx}
                    if choice.get('choiceId'):
                        params['choiceId'] = choice['choiceId'][0]
                    add('choice', **params)
            # Mandatory choices remain explicit; ordinary prep only without a pending choice.
            elif state.get('phase') == 'PREP':
                for kind, field in (('buy', 'slot'), ('reward', 'idx'), ('sell', 'uid'), ('destroy', 'uid')):
                    for target in ops.get('g.'+kind, {}).get(field, [])[:64]:
                        add(kind, **{field: target})
                for kind in ('refresh', 'freeze', 'levelUp'):
                    if 'g.'+kind in ops:
                        add(kind)
                if True in ops.get('g.ready', {}).get('ready', []):
                    add('ready', ready=True)
                move = ops.get('g.move', {})
                tiles = _tiles(move, own.get('deployment'))
                empty = [i for i, p in enumerate(own.get('hand', [])) if p is None][:2]
                # Reserve sources first so a large board doesn't mask deployment.
                ordered = [p for area in ('temp', 'hand', 'board') for p in own.get(area, []) if p]
                for p in [p for p in ordered if p['uid'] in move.get('uid', [])][:16]:
                    locations = list(tiles)
                    if 'row' in p and (p['row'], p['col']) not in locations:
                        locations.append((p['row'], p['col']))
                    for row, col in locations:
                        for direction in move.get('dir', ['RIGHT']):
                            if (row, col, direction) != (p.get('row'), p.get('col'), p.get('dir')):
                                add('move', uid=p['uid'], to={'area': 'board', 'row': row, 'col': col}, dir=direction)
                    for idx in empty:
                        if not (idx < len(own.get('hand', [])) and own['hand'][idx] and own['hand'][idx]['uid'] == p['uid']):
                            add('move', uid=p['uid'], to={'area': 'hand', 'idx': idx})
                equip = ops.get('g.equip', {})
                by_uid = {p['uid']: p for p in pieces}
                for item in equip.get('itemUid', [])[:8]:
                    for target in equip.get('targetUid', [])[:8]:
                        add('equip', itemUid=item, targetUid=target)
                        for old in by_uid.get(target, {}).get('items', []):
                            add('equip', itemUid=item, targetUid=target, replaceUid=old['uid'])
                art = ops.get('g.art', {})
                for item in art.get('itemUid', [])[:4]:
                    for row, col in tiles:
                        for direction in art.get('dir', ['RIGHT']):
                            add('art', itemUid=item, row=row, col=col, dir=direction)
    blocked = blocked or set()
    seen = set()
    actions = [a for a in actions if action_key(a) not in blocked
               and not (action_key(a) in seen or seen.add(action_key(a)))]
    by_uid = {p['uid']: p for p in pieces}
    features = []
    item_by_uid = {item['uid']: item for p in pieces for item in p.get('items', [])}
    item_by_uid.update(by_uid)
    choice_cards = (own.get('personalChoice') or public.get('sp') or {}).get('cards', [])
    for action in actions:
        kind = action['type']
        source = item_by_uid.get(action.get('uid', action.get('itemUid')), {})
        target = by_uid.get(action.get('targetUid'), {})
        slot = action.get('slot') if kind == 'buy' else action.get('idx') if kind == 'reward' else None
        slots = shop.get('slots', []) if kind == 'buy' else (shop.get('rewardOffer') or {}).get('slots', [])
        offer = slots[slot] if isinstance(slot, int) and slot < len(slots) else {}
        offer = offer or {}
        to = action.get('to', {})
        nums = [offer.get('price', 0)/30, source.get('tier', 0)/6 if source.get('tier') else 0,
                float(source.get('golden', False)), len(source.get('items', []))/2,
                source.get('row', -1)/20, source.get('col', -1)/10,
                to.get('row', action.get('row', -1))/20, to.get('col', action.get('col', -1))/10,
                float(to.get('area') == 'hand'), to.get('idx', -1)/10,
                len(target.get('items', []))/2, float('replaceUid' in action),
                float(kind == 'ready'), float(kind == 'wait'), target.get('row', -1)/20, target.get('col', -1)/10]
        semantic = {k: v for k, v in action.items() if k not in ('uid', 'itemUid', 'targetUid', 'replaceUid', 'revision')}
        choice_idx = action.get('idx')
        selected_card = choice_cards[choice_idx] if kind == 'choice' and isinstance(choice_idx, int) and 0 <= choice_idx < len(choice_cards) else None
        semantic.update(source=source.get('id'), target=target.get('id'), offer=offer,
                        selected_card=selected_card,
                        replaced_item=item_by_uid.get(action.get('replaceUid'), {}).get('id'),
                        reference=(references or {}).get(source.get('id'), None))
        features.append([float(kind == t) for t in TYPES] + [_number(n) for n in nums]
                        + _hash_features(semantic, 64))
    return Decision(obs, features, actions, bool(state.get('terminal')))


def tensorize(decision):
    return (torch.tensor(decision.obs, dtype=torch.float32),
            torch.tensor(decision.action_features, dtype=torch.float32).reshape(-1, ACTION_DIM))


class ActorCritic(nn.Module):
    def __init__(self):
        super().__init__()
        self.actor = nn.Sequential(nn.Linear(OBS_DIM+ACTION_DIM, 128), nn.Tanh(), nn.Linear(128, 64), nn.Tanh(), nn.Linear(64, 1))
        self.critic = nn.Sequential(nn.Linear(OBS_DIM, 64), nn.Tanh(), nn.Linear(64, 1))

    def forward(self, obs_tensor, action_tensor):
        obs = obs_tensor.reshape(OBS_DIM)
        logits = self.actor(torch.cat((obs.expand(action_tensor.shape[0], -1), action_tensor), dim=-1)).squeeze(-1)
        return logits, self.critic(obs).squeeze(-1)


def seed_all(seed):
    random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)


class PlayerEnv:
    def __init__(self, seed=20001, mode='coop', difficulty='ABYSS', partners='mixed',
                 reward_mode='potential', gamma=.99, max_steps=2048, directory=None, root=None):
        if reward_mode not in ('sparse', 'potential') or not 0 <= gamma <= 1 or max_steps < 1:
            raise ValueError('Invalid environment reward/gamma/step configuration')
        self.next_seed, self.mode, self.difficulty, self.partners = seed, mode, difficulty, partners
        self.reward_mode, self.gamma, self.max_steps = reward_mode, gamma, max_steps
        self.directory = Path(directory) if directory is not None else None
        self.root, self.session, self.state, self.decision = root, None, None, None
        self.deadline = None  # make_env attaches the trainer's total wall deadline.
        self.last_info = {}
        self.episode = 0

    def _remaining(self):
        remaining = 120. if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Training wall budget exhausted')
        return min(120., remaining)

    def _bounded_call(self, function, *args):
        self.session.timeout = self._remaining()
        child = getattr(self.session, '_child', None)
        if child is not None:
            child.timeout = self.session.timeout
        return function(*args)

    def reset(self, seed=None):
        self.close()
        self.seed = self.next_seed if seed is None else seed
        self.next_seed = self.seed + 1
        self.episode += 1
        self.steps, self.requests, self.no_progress = 0, 1, 0
        self.blocked = set()
        directory = self.directory / f'episode-{self.episode:06d}-seed-{self.seed}-{uuid.uuid4().hex[:8]}' if self.directory else None
        self.session = Session(root=self.root, directory=directory, timeout=self._remaining(), max_requests=8192,
                               seed=self.seed, mode=self.mode, difficulty=self.difficulty, partners=self.partners)
        try:
            self.session.__enter__()
            self.state = self._bounded_call(self.session.observe)
            self.requests += 1
            self.decision = build_decision(self.state)
            if not self.decision.actions and not self.decision.terminal:
                raise RuntimeError('No candidates for a live player decision')
            self.last_info = {'seed': self.seed, 'status': 'running', 'steps': 0}
            return self.decision
        except BaseException:
            self.close()
            raise

    def step(self, index):
        if self.session is None or self.decision is None or self.decision.terminal:
            raise RuntimeError('Reset required before step')
        if not isinstance(index, int) or not 0 <= index < len(self.decision.actions):
            raise IndexError('Candidate index outside captured mask')
        action = self.decision.actions[index]
        previous = self.state
        info = {'seed': self.seed, 'action': action, 'accepted': True, 'technical_failure': False}
        terminated = truncated = False
        reward = 0.
        try:
            if self.requests >= 8192:
                raise RuntimeError('Environment request budget exhausted')
            if action['type'] == 'wait':
                state = self._bounded_call(self.session.wait)
            else:
                reply = self._bounded_call(self.session.act, action)
                state, info['accepted'] = reply['state'], reply['accepted']
                info['result'] = reply.get('result')
            self.requests += 1
            self.steps += 1
            same = fingerprint(previous) == fingerprint(state)
            self.no_progress = self.no_progress + 1 if same else 0
            if not same:
                self.blocked.clear()
            elif not info['accepted']:
                self.blocked.add(action_key(action))
            self.state = state
            terminated = bool(state['terminal'])
            if terminated:
                from arknights.evaluate.runner import team_victory
                sparse = 1. if team_victory(state) else -1.
            else:
                sparse = 0.
            reward = sparse
            if self.reward_mode == 'potential':
                reward += self.gamma * potential(state) - potential(previous)
            self.decision = build_decision(state, blocked=self.blocked)
            if not terminated and not self.decision.actions:
                raise RuntimeError('Live decision shortlist exhausted after rejections')
            if not terminated and (self.steps >= self.max_steps or self.no_progress >= 64
                                   or self.requests >= 8192 or self.deadline is not None and time.monotonic() >= self.deadline):
                truncated = True
                info['reason'] = ('max_steps' if self.steps >= self.max_steps else 'no_progress' if self.no_progress >= 64
                                  else 'request_budget' if self.requests >= 8192 else 'max_wall')
        except Exception as exc:
            terminated, truncated = False, True
            wall_expired = self.deadline is not None and time.monotonic() >= self.deadline
            info.update(technical_failure=not wall_expired,
                        reason='max_wall' if wall_expired else f'{type(exc).__name__}: {exc}')
        info.update(steps=self.steps, requests=self.requests, terminated=terminated, truncated=truncated,
                    status='complete' if terminated else 'failed' if info['technical_failure'] else 'truncated' if truncated else 'running')
        if terminated:
            info['outcome'] = self.state.get('outcome')
        self.last_info = info
        if terminated or truncated:
            self.close()
        return self.decision, reward, terminated, truncated, info

    def close(self):
        session, self.session = self.session, None
        if session is not None:
            session.close()


def make_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--steps', type=int, default=16384)
    parser.add_argument('--train-seed', type=int, default=0)
    parser.add_argument('--first-game-seed', type=int, default=20001)
    parser.add_argument('--mode', choices=('solo', 'coop'), default='coop')
    parser.add_argument('--partners', choices=('original', 'mixed', 'weak'), default='mixed')
    parser.add_argument('--reward', choices=('sparse', 'potential'), default='potential')
    parser.add_argument('--gamma', type=float, default=.99)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-wall', type=float, default=600.)
    parser.add_argument('--max-episode-steps', type=int, default=2048)
    return parser


def make_env(args):
    env = PlayerEnv(seed=args.first_game_seed, mode=args.mode, partners=args.partners,
                    reward_mode=args.reward, gamma=args.gamma, max_steps=args.max_episode_steps,
                    directory=Path(args.out)/'trajectories')
    env.deadline = time.monotonic() + args.max_wall
    return env


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_policy(checkpoint_path, policy_path):
    checkpoint_path, policy_path = Path(checkpoint_path).resolve(), Path(policy_path).resolve()
    payload = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    hashes = payload['metadata']['source_hashes']
    # Paths are relative to the exported script, so relocating the entire checkout works.
    common = Path(__file__).resolve()
    sources = {os.path.relpath(Path(path), policy_path.parent): digest for path, digest in hashes.items()}
    common_rel = os.path.relpath(common, policy_path.parent)
    checkpoint_rel = os.path.relpath(checkpoint_path, policy_path.parent)
    script = '''"""Frozen artifact policy: verifies model, environment, and algorithm before loading."""
from pathlib import Path
import hashlib
import sys
import types
import torch
_BASE = Path(__file__).resolve().parent
_SOURCES = SOURCES
_CHECKPOINT = CHECKPOINT
_MODEL_HASH = MODEL_HASH
_COMMON = COMMON
_MODEL = None
_API = None

def _verified(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('RL artifact provenance mismatch: ' + str(path))
    return data

def play(game):
    global _MODEL, _API
    captured = {name: _verified(_BASE / name, digest) for name, digest in _SOURCES.items()}
    model_bytes = _verified(_BASE / _CHECKPOINT, _MODEL_HASH)
    if _MODEL is None:
        import io
        name = '_frozen_rl_common_' + _SOURCES[_COMMON][:16]
        module = types.ModuleType(name)
        module.__file__ = str((_BASE / _COMMON).resolve())
        sys.modules[name] = module
        exec(compile(captured[_COMMON], module.__file__, 'exec'), module.__dict__)
        _API = module
        _MODEL = module.ActorCritic()
        payload = torch.load(io.BytesIO(model_bytes), map_location='cpu', weights_only=True)
        _MODEL.load_state_dict(payload['state_dict'])
        _MODEL.eval()
        torch.set_num_threads(1)
    state = game.observe()
    blocked, previous, no_progress = set(), None, 0
    for _ in range(8188):
        if state['terminal']:
            return
        mark = _API.fingerprint(state)
        if mark != previous:
            blocked.clear()
            no_progress = 0
        else:
            no_progress += 1
        previous = mark
        if no_progress >= 64:
            raise RuntimeError('RL inference made no progress')
        decision = _API.build_decision(state, blocked=blocked)
        if not decision.actions:
            raise RuntimeError('RL inference shortlist exhausted')
        with torch.no_grad():
            logits, _ = _MODEL(*_API.tensorize(decision))
            index = int(logits.argmax().item())
        action = decision.actions[index]
        if action['type'] == 'wait':
            state = game.wait()
        else:
            reply = game.act(action)
            if not reply['accepted']:
                blocked.add(_API.action_key(action))
            state = reply['state']
    raise RuntimeError('RL inference request budget exhausted')
'''
    # Replace whole sentinel tokens once; replacement text cannot change other fields.
    values = {'SOURCES': repr(sources), 'CHECKPOINT': repr(checkpoint_rel),
              'MODEL_HASH': repr(_sha(checkpoint_path)), 'COMMON': repr(common_rel)}
    script = re.sub(r'\b(SOURCES|CHECKPOINT|MODEL_HASH|COMMON)\b', lambda m: values[m[0]], script)
    policy_path.write_text(script)
    return policy_path


def finish_training(out_dir, model, summary):
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(json.dumps(summary, allow_nan=False))
    algorithm = str(metadata.get('algorithm', '')).lower()
    source = Path(__file__).with_name(algorithm+'.py')
    if algorithm not in ('ppo', 'a2c', 'reinforce') or not source.is_file():
        raise ValueError('summary.algorithm must identify the actual algorithm source')
    metadata.setdefault('actual_steps', metadata.get('steps', 0))
    metadata.setdefault('episodes', metadata.get('games_played',
        metadata.get('episodes_completed', 0) + metadata.get('episodes_truncated', 0) + metadata.get('episodes_failed', metadata.get('technical_failures', 0))))
    metadata.setdefault('optimizer_updates', 0)
    metadata.update(obs_dim=OBS_DIM, action_dim=ACTION_DIM, rules=RULES, cold_start=True,
                    shortlist=SHORTLIST, rejection_penalty=0.,
                    potential='0 at terminal; .1*clip(round/20,0,1)+.1*tanh(max activated CORE bond layers/100)',
                    core_bond_ids=sorted(CORE_BONDS),
                    source_hashes={str(Path(__file__).resolve()): _sha(__file__), str(source.resolve()): _sha(source)})
    target = out/'checkpoint.pt'
    temporary = out/f'.checkpoint-{uuid.uuid4().hex}.tmp'
    try:
        torch.save({'state_dict': model.state_dict(), 'metadata': metadata}, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    policy = export_policy(target, out/'policy.py')
    metadata.update(checkpoint_sha256=_sha(target), policy_sha256=_sha(policy))
    summary_path = out/'summary.json'
    summary_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    return {'checkpoint': str(target), 'policy': str(policy), 'summary': str(summary_path)}
