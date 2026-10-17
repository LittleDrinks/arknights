import assert from 'node:assert/strict';
import test from 'node:test';
import { ShopEnv } from '../rl/env.mjs';
import { CoopEnv } from './env.mjs';
import { poolConfig } from './pool.mjs';
import { loadMovePreflight } from './preflight.mjs';
import { rejectionState } from './rejections.mjs';
import { capacityFilteredBot } from './controller.mjs';

const board = (row, col) => ({ area: 'board', row, col });
const chess = (uid, id, dir = 'RIGHT') => ({ uid, id, kind: 'chess', dir, items: [], meta: {} });
const token = (uid, ownerUid, id = 'token_10028_vigil_wolf') => ({ uid, ownerUid, id, kind: 'token', count: 1, dir: 'RIGHT' });

test('independent placement fixtures: every BAD_TILE branch, reverse swaps and summon exceptions', async () => {
  const base = await ShopEnv.create(), preflight = await loadMovePreflight();
  // Explicit synthetic fixtures, NOT clones of a live match or snapshot restore.
  const fixture = spec => {
    const sched = new base.VirtualScheduler();
    const m = new base.Match({ roomCode: 'FIXTURE', seed: 11, mode: 'solo', difficulty: 'NORMAL', data: base.data, scheduler: sched,
      seats: [{ seat: 0, playerId: 'fixture', isBot: true }], send: () => true, broadcast() {}, onEnd() {},
      log: { info() {}, warn() {}, error() {}, debug() {} } });
    m.phase = 'PREP'; m.round = 1;
    m.stage = base.data.stages.act2autochess_m04;
    const ps = m.order[0];
    ps.board = new Map(structuredClone(spec.board || []));
    ps.hand.fill(null); (spec.hand || []).forEach((p, i) => { ps.hand[i] = structuredClone(p); });
    ps.temp.fill(null); (spec.temp || []).forEach((p, i) => { ps.temp[i] = structuredClone(p); });
    // Current terrain overrides exercise return-tile legality independently of destination legality.
    ps.tileOverrides = { '10,4': 'melee', '10,5': 'melee', '9,4': 'ranged', ...spec.tiles };
    ps._deployMap = null;
    if (spec.stale) {
      ps._deployMap = new Map([['10,6', 'melee']]);
      ps._deployField = 'bossL'; ps._legalityStale = false;
    }
    return { m, ps, close() { m.dispose(); sched.dispose(); } };
  };
  const ranged = chess(1, 'chess_char_3_19_a'); // Vigil, range-bound wolves.
  const probe = fixture({});
  const meleeId = Object.keys(base.data.chess).find(id => probe.ps._placementOf(chess(2, id)) === 'melee');
  probe.close();
  assert(meleeId);
  const melee = chess(2, meleeId), wolf = token(3, 1);
  const cases = [
    ['destination-out-of-field', { hand: [ranged] }, 1, board(-1, 5)],
    ['destination-illegal', { hand: [ranged] }, 1, board(10, 6), 'LEFT'],
    ['destination-illegal', { temp: [ranged] }, 1, board(11, 6), 'DOWN'],
    ['destination-illegal', { hand: [ranged], stale: true }, 1, board(10, 6), 'LEFT'],
    ['swap-return-illegal', { board: [['9,4', ranged], ['10,5', melee]] }, 1, board(10, 5)],
    ['hand-swap-return-illegal', { board: [['9,4', ranged]], hand: [melee] }, 1, { area: 'hand', idx: 0 }],
    ['destination-illegal', { board: [['10,4', ranged]], hand: [wolf] }, 3, board(12, 2)],
    ['summon-destination-occupied', { board: [['10,4', ranged], ['10,5', melee]], hand: [wolf] }, 3, board(10, 5)],
    // Forward destination is legal; the OTHER owner's summon cannot return to source.
    ['swap-return-illegal', { board: [['10,4', ranged], ['10,5', wolf], ['12,2', chess(4, ranged.id)]] }, 4, board(10, 5)],
    // Owner-after facing matters: wolf at right, owner still faces right after swapping onto its tile.
    ['summon-owner-swap-destination', { board: [['10,4', ranged], ['10,5', wolf]] }, 3, board(10, 4)],
    ['swap-return-illegal', { board: [['9,4', token(3, 1, 'token_10032_jesca2_jckshd')], ['10,4', melee]] }, 3, board(10, 4)],
    // Own summon is lifted, so a melee wolf need not be legal on its owner's old high tile.
    [null, { board: [['9,4', ranged], ['10,4', wolf]] }, 1, board(10, 4)],
    [null, { board: [['10,4', ranged], ['10,5', wolf]] }, 1, board(10, 4)],
    [null, { hand: [ranged] }, 1, board(10, 4)],
    [null, { board: [['10,4', ranged], ['10,5', melee]] }, 1, board(10, 5)],
    [null, { board: [['10,4', chess(1, ranged.id, 'LEFT')], ['10,5', wolf]] }, 3, board(10, 4)],
  ];
  for (const [reason, spec, uid, to, dir] of cases) {
    const a = fixture(spec), b = fixture(spec);
    try {
      const before = rejectionState(a.m), cache = a.ps._deployMap;
      const state = structuredClone([a.ps.board, a.ps.hand, a.ps.temp]);
      assert.equal(preflight(a.ps, uid, to, dir), reason, JSON.stringify([reason, to]));
      assert(before.equals(rejectionState(a.m)), 'preflight changed state');
      assert.equal(a.ps._deployMap, cache, 'preflight warmed live cache');
      const result = b.ps.move(uid, to, dir);
      if (reason) {
        assert.equal(result.error, 'BAD_TILE');
        assert.deepEqual([b.ps.board, b.ps.hand, b.ps.temp], state, 'native rejection changed pieces');
      } else assert.equal(result.ok, true, JSON.stringify(result));
    } finally { a.close(); b.close(); }
  }
  const f = fixture({ hand: [ranged] });
  try {
    // Gate, bad uid, item and invalid dir keep their native non-BAD_TILE path.
    for (const args of [[-1, board(10, 6)], [1, board(10, 6), 'BAD'], [1, { area: 'hand', idx: -1 }]]) {
      assert.equal(preflight(f.ps, ...args), null);
      assert.equal(f.ps.move(...args).error, 'BAD_TARGET');
    }
    f.ps.ready = true;
    assert.equal(preflight(f.ps, 1, board(10, 6)), null);
    assert.equal(f.ps.move(1, board(10, 6)).error, 'WRONG_PHASE');
  } finally { f.close(); }
});

test('two real resets and the same buy prefix prove filtered water moves reject natively', async () => {
  const base = await ShopEnv.create();
  base.movePreflight = await loadMovePreflight();
  base.bot = { arrange() {} };
  const config = poolConfig({ COOP_POOL: 'normal' });
  const a = new CoopEnv(base, config), b = new CoopEnv(base, config);
  try {
    const start = e => {
      const s = e.reset({ seed: 11, difficulty: 'ABYSS', budget: 4 });
      e.step(s.candidates.findIndex(c => c.type === 'buy'));
      return e.ps.hand.find(p => p?.kind === 'chess').uid;
    };
    const uid = start(a);
    assert.equal(start(b), uid);
    assert.deepEqual(a.ps.privateView(), b.ps.privateView());
    for (const [to, dir] of [[board(10, 6), 'LEFT'], [board(11, 6), 'DOWN']]) {
      const before = structuredClone(b.ps.board);
      assert.equal(Object.getPrototypeOf(b.ps).move.call(b.ps, uid, to, dir).error, 'BAD_TILE');
      assert.deepEqual(b.ps.board, before);
      let result;
      const calls = a.history.actions.length, errors = a.controllerErrors;
      const guard = rejectionState(a.m);
      capacityFilteredBot({ arrange: (m, p) => { result = p.move(uid, to, dir); } },
        r => { a.controllerBlockedMoves++; a.controllerBlockedMoveReasons.push(r); }, a.arranging, a.movePreflight).arrange(a.m, a.ps);
      assert.equal(result.error, 'COOP_BAD_TILE_FILTER');
      assert(guard.equals(rejectionState(a.m)));
      assert.equal(a.history.actions.length, calls);
      assert.equal(a.controllerErrors, errors);
    }
    assert.equal(a.controllerBlockedMoves, 2);
    const p = a.m.order[1], audit = a.partnerAudit[p.playerId];
    // A bad target remains an actual rejection; a known bad tile is separately filtered.
    p.hand[0] = chess(9876, a.ps.hand.find(p => p?.kind === 'chess').id);
    const hash = audit.hash.copy().digest('hex'), state = rejectionState(a.m);
    assert.equal(p.move(9876, board(10, 6), 'LEFT').error, 'COOP_BAD_TILE_FILTER');
    assert(state.equals(rejectionState(a.m)));
    assert.equal(audit.filtered, 1);
    assert.equal(audit.rejected, 0);
    assert.equal(a.partnerActionErrors, 0);
    assert.equal(audit.hash.copy().digest('hex'), hash);
    assert.equal(p.move(-1, board(10, 6)).error, 'BAD_TARGET');
    assert.equal(audit.rejected, 1);
    assert.equal(a.partnerActionErrors, 1);
    assert(!('partnerAudit' in a.view()));
    assert(!('diagnostics' in a.view().episode));
  } finally { a.close(); b.close(); }
});

test('production factory loads preflight; a swallowed preflight exception stays fatal', async () => {
  const base = await CoopEnv.create();
  assert.equal(typeof base.movePreflight, 'function');
  const e = new CoopEnv({ ...base, bot: { arrange(m, ps) {
    try { ps.move(-1, board(10, 4)); } catch {}
  } } }, base.poolConfig);
  try {
    e.movePreflight = () => { throw new Error('preflight crash'); };
    assert.throws(() => e.reset({ seed: 11, difficulty: 'ABYSS' }), /Invalid coop rollout/);
    assert.equal(e.fatalRejections, 1);
    assert.equal(e.arranging.has(e.ps), false);
  } finally { e.close(); base.close(); }
});
