import test from 'node:test';
import assert from 'node:assert/strict';
import { History, candidates, bind, reduceEvents, eventTree, exact, copy } from './core.mjs';
import { encode, makeSchema } from '../rl/features.mjs';
import { ShopEnv } from '../rl/env.mjs';

const data = { chess: { a: { tier: 1, bonds: ['b'] }, gold: { tier: 2, isGolden: true },
  diy: { tier: 1, isDiy: true }, validDiy: { tier: 2, isDiy: true, diyFor: 'pick' } },
  items: { item: { tier: 3 } }, bonds: { b: {} }, bands: { band: {} } };
const gd = { chess: id => data.chess[id], item: id => data.items[id], sellPrice: id => id === 'gold' ? 3 : 1 };
const schema = makeSchema(data);
const piece = (uid, id = 'a') => ({ uid, id, kind: 'chess', golden: id === 'gold' });
function obs(round = 1) {
  return { public: { round, phase: 'PREP', difficulty: 'ABYSS', serverNow: 999, deadline: 1000 },
    own: { playerId: 'partner-1', seat: 1, alive: true, ready: false, canReady: true, lp: 20, funds: 5,
      shop: { level: 1, maxLevel: 6, upgradePrice: 5, refreshPrice: 1, freeRefreshes: 0, frozen: false,
        slots: [{ kind: 'chess', id: 'a', price: 2 }] },
      board: [piece('old')], hand: [null, null], temp: [], bonds: [], effects: [],
      deployCount: 1, deployCap: 8, bandId: 'band' } };
}
function event(seq, method, before = obs(), after = copy(before), extra = {}) {
  const action = method === 'buy' ? { t: 'g.buy', slot: 0 } : method === 'sell' ? { t: 'g.sell', uid: 'old' }
    : method === 'setReady' ? { t: 'g.ready', ready: true } : { t: `g.${method}` };
  return { seq, parentSeq: null, playerId: 'partner-1', source: 'partner-controller', method,
    observation: before, next_observation: after, action, accepted: true, result: { ok: true }, ...extra };
}

test('nested calls encode entry, children complete, then parent updates totals/byId/exposure', () => {
  const initial = obs(), childBefore = obs(), childAfter = obs();
  childAfter.own.hand[0] = piece('new');
  const events = [event(0, 'refresh', initial, childAfter),
    event(1, 'buy', childBefore, childAfter, { parentSeq: 0 }),
    event(2, 'sell', childAfter)];
  const seen = [];
  const result = reduceEvents(events, { gd, schema, budget: 8,
    onDecision(e, sample, cs, candidate, h) { seen.push({ seq: e.seq, h: copy(h.h), sample, candidate }); } });
  assert.equal(seen[1].h.totals.refresh, undefined, 'parent must not update before child entry');
  assert.equal(seen[1].h.prepSteps, 0);
  assert.equal(seen[2].h.prepSteps, 2);
  assert.deepEqual(seen[2].h.byId, { a: { buys: 1 } });
  assert.deepEqual(seen[2].h.rounds[1].byId, { a: { buys: 1 } });
  assert.equal(seen[2].h.pieces.old.refreshes, 1);
  assert.equal(seen[2].h.pieces.old.boardRefreshes, 1);
  assert.equal(seen[2].h.pieces.new.refreshes, 0, 'refresh exposes only pieces present before parent entry');
  assert.equal(seen[2].h.pieces.new.firstRound, 1);
  assert.deepEqual(result.histories.get('partner-1').h.actions.map(a => a.type), ['buy', 'refresh', 'sell']);
  assert.deepEqual(result.histories.get('partner-1').h.byId, { a: { buys: 1, sells: 1 } });
});

test('observe matches original observe, retains firstRound/byId while resetting per-round exposure', () => {
  const h = new History(8, 'observable-teammate-teacher');
  const first = obs(); h.observe(first); h.finish(event(0, 'refresh', first), first, true);
  const saved = structuredClone(h.h);
  const second = obs(2); second.own.board.push(piece('born2'));
  const facade = { prepRound: 1, history: saved, lastBondState: h.lastBondState,
    ps: { privateView: () => copy(second.own) }, m: { publicView: () => copy(second.public) } };
  exact(h.observe(second), ShopEnv.prototype.observe.call(facade), 'original observe');
  assert.equal(h.h.pieces.old.firstRound, 1); assert.equal(h.h.pieces.born2.firstRound, 2);
  assert.equal(h.h.pieces.old.refreshes, 0); assert.equal(h.h.prepSteps, 0);
  assert.equal(h.h.rounds[1].refresh, 1); assert.equal(h.h.totals.refresh, 1);
});

test('actual policy budget is outside recorder; rewards, controller sales and cleanup do not increment it', () => {
  const o = obs(); o.own.playerId = 'learner';
  const cs = candidates(new History(8, 'actual-primary-policy').observe(o), gd);
  const index = cs.findIndex(c => c.type === 'refresh');
  const policy = { traceIndex: 0, candidateIndex: index, candidateCount: cs.length, candidate: cs[index] };
  const events = [event(0, 'refresh', o, copy(o), { playerId: 'learner', source: 'policy', policy }),
    event(1, 'pickReward', o, copy(o), { playerId: 'learner', source: 'fixed-controller' }),
    event(2, 'resolveTemp', o, copy(o), { playerId: 'learner', source: 'budget-controller' }),
    event(3, 'sell', o, copy(o), { playerId: 'learner', source: 'policy', parentSeq: 2 }),
    event(4, 'setReady', o, copy(o), { playerId: 'learner', source: 'budget-controller' })];
  const { histories } = reduceEvents(events, { gd, schema, budget: 8 });
  const h = histories.get('learner');
  assert.equal(h.steps, 1); assert.equal(h.h.prepSteps, 1);
  assert.equal(h.h.totals.sell, 1); assert.equal(h.h.totals.reward, 1); assert.equal(h.h.totals.ready, 1);
  assert.equal(h.h.totals.resolveTemp, undefined);
  // Same engine cleanup under a teammate's controller: no hypothetical decision increment either.
  const cleanup = [event(0, 'resolveTemp'), event(1, 'sell', obs(), obs(), { parentSeq: 0 })];
  const teacher = reduceEvents(cleanup, { gd, schema, budget: 8 }).histories.get('partner-1');
  assert.equal(teacher.steps, 0); assert.equal(teacher.h.totals.sell, 1);
});

test('teacher and actual policy increment on different sides of a round-changing after-observe', () => {
  const before = obs(1), after = obs(2);
  const teacher = new History(8, 'observable-teammate-teacher'); teacher.observe(before);
  teacher.finish(event(0, 'setReady', before, after), before, true);
  assert.equal(teacher.h.prepSteps, 0); assert.equal(teacher.steps, 1);
  const policy = new History(8, 'actual-primary-policy'); policy.observe(before);
  policy.finish(event(0, 'setReady', before, after, { policy: {} }), before, false);
  assert.equal(policy.h.prepSteps, 1);
});

test('candidate legality, full fields and mask: buy/sell/refresh/levelUp/ready', () => {
  const raw = obs(), h = new History(8, 'observable-teammate-teacher');
  raw.own.shop.slots.push({ kind: 'chess', id: 'diy', price: 1 }, { kind: 'chess', id: 'validDiy', price: 2 },
    { kind: 'item', id: 'item', price: 3 }, { kind: 'chess', id: 'missing', price: 1 },
    { kind: 'chess', id: 'gold', price: 6 }, { kind: 'chess', id: 'a', price: 1, sold: true });
  raw.own.temp = [piece('elite', 'gold'), { kind: 'item', id: 'item', uid: 'equipment' }];
  const before = h.observe(raw), cs = candidates(before, gd), f = encode(before, cs, schema);
  assert.deepEqual(cs.map(c => c.type), ['ready', 'refresh', 'levelUp', 'buy', 'buy', 'buy', 'sell', 'sell']);
  assert.deepEqual(cs.at(-1), { type: 'sell', uid: 'elite', id: 'gold', price: 3, tier: 2, golden: true, bonds: [] });
  assert.equal(f.mask.length, cs.length); assert(f.mask.every(x => x === true));
  const e = event(0, 'buy', raw); const b = bind(e, cs);
  assert.equal(b.action, 3); assert.deepEqual(b.candidate, cs[3]);
  assert.equal(bind({ ...e, accepted: false }, cs).reason, 'rejected');
  assert.equal(bind({ ...e, action: { t: 'g.buy', slot: 4 } }, cs).reason, 'no-unique-legal-candidate');
  assert.equal(bind(event(0, 'setReady', raw, raw, { action: { t: 'g.ready', ready: false } }), cs).reason, 'not-ready-true');
  assert.throws(() => bind({ ...e, policy: { candidate: { ...cs[3], price: 999 } } }, cs), /candidate all fields.*price/);
  raw.own.hand = [piece('h1'), piece('h2')]; assert(!candidates(h.observe(raw), gd).some(c => c.type === 'buy'));
  raw.own.funds = 0; raw.own.canReady = false;
  assert(candidates(h.observe(raw), gd).every(c => c.type === 'sell'));
  raw.own.shop.freeRefreshes = 1; assert(candidates(h.observe(raw), gd).some(c => c.type === 'refresh'));
  raw.own.ready = true; assert.deepEqual(candidates(h.observe(raw), gd), []);
  raw.own.ready = false; raw.own.alive = false; assert.deepEqual(candidates(h.observe(raw), gd), []);
  raw.own.alive = true; raw.public.phase = 'RESULT'; assert.deepEqual(candidates(h.observe(raw), gd), []);
});

test('rejected/unmapped/non-actions are accounted for without fabricated labels', () => {
  const events = [event(0, 'buy', obs(), obs(), { accepted: false, result: { ok: false } }),
    event(1, 'buy', obs(), obs(), { action: { t: 'g.buy', slot: 99 } }), event(2, 'move')];
  let labels = 0;
  const r = reduceEvents(events, { gd, schema, budget: 8, onDecision() { labels++; } });
  assert.equal(labels, 0);
  assert.deepEqual(r.dispositions.map(d => d.reason), ['rejected', 'no-unique-legal-candidate', 'non-shop-method']);
  assert.equal(r.histories.get('partner-1').steps, 1, 'accepted unmapped method still belongs to observed teacher history');
});

test('malformed trees and strict first scalar difference fail explicitly', () => {
  assert.throws(() => eventTree([event(0, 'buy'), event(1, 'sell'), event(2, 'refresh', obs(), obs(), { parentSeq: 0 })]), /closed\/missing/);
  assert.throws(() => eventTree([event(1, 'buy')]), /non-contiguous/);
  assert.throws(() => exact({ features: { global: [1, 2] } }, { features: { global: [1, 3] } }, 'primary step=7 seq=99'),
    /primary step=7 seq=99.*first difference.*global.*actual.*2.*expected.*3/);
});
