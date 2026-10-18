"""Tiny synthetic demo, used only by tests and the fake line server."""
from reuse import C

SCHEMA = dict(version='shop-v1', ids=[], bonds=[], bands=[])
GAME = dict(schema=SCHEMA, rules=C.RULES, dataSHA256='d'*64)
POOL = 'a'*64


def demo(seed, pool='normal', alive=True, rejected=0):
    audits = {f'partner-{i}': dict(rejected=0, rejections=[]) for i in (1, 2, 3)}
    diag = dict(partnerAudit=audits, partnerActionErrors=0, controllerDiagnosticErrors=0,
                rejectedActions=[], controllerBlockedMoves=0, controllerBlockedMoveReasons=[], filteredMoves=[])
    team = C.TEAMS['original' if pool == 'normal' else 'mixed']
    families = dict(zip(('original-v1', 'buy2-v1', 'saver-v1'), ('upstream-bot', 'first-offer-buy2', 'level-first-saver')))
    partners = dict(pool=pool, split='train', poolSeed=313, fixed=team, manifest='coop-heuristics-v1',
                    manifestSHA256=POOL, seats=[dict(seat=i+1, id=x, family=families[x]) for i, x in enumerate(team)])
    players = [dict(playerId='learner' if i == 0 else f'partner-{i}', seat=i, victory=alive or i > 0,
                    alive=alive or i > 0, roundsPassed=14 if alive or i > 0 else 10,
                    eliminatedRound=None if alive or i > 0 else 11, lp=1 if alive or i > 0 else 0,
                    lineup=[], bonds={}, stats={}) for i in range(4)]
    terminal = dict(done=True, schema=SCHEMA, rules=C.RULES, steps=2+rejected, rounds=14, reward=1, victory=True,
                    episode=dict(seed=seed, mode='coop', difficulty='ABYSS', budget=128, diagnostics=diag, partners=partners),
                    summary=dict(seed=seed, modeId='mode_multi_abyss', difficulty='ABYSS', victory=True, errors=0,
                                 roundsPassed=14, reason='victory', players=players),
                    observation=dict(public=dict(phase='RESULT'), own=dict(playerId='learner', seat=0, alive=alive,
                                                                          lp=1 if alive else 0)),
                    features={'global': [0]*90, 'candidates': [], 'mask': []}, candidates=[],
                    engineErrors=0, actionErrors=0, controllerErrors=0, **diag)
    return dict(ok=True, schema=SCHEMA, rules=C.RULES, terminal=terminal,
                samples=[dict(features={'global': [0]*90, 'candidates': [[0]*15], 'mask': [True]}, action=0, round=i+1)
                         for i in range(2)], teacher='upstream-bot/rehearsal=0', rejectedLabels=rejected)
