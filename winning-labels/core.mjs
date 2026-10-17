// Offline reduction of recorded calls, never execution of game methods.
import assert from 'node:assert/strict';
import { ShopEnv } from '../rl/env.mjs';
import { encode, pieces, TYPES } from '../rl/features.mjs';

const types = { buy: 'buy', sell: 'sell', refresh: 'refresh', levelUp: 'levelUp', setReady: 'ready',
  move: 'move', equip: 'equip', pickReward: 'reward', useArt: 'art', destroy: 'destroy', freeze: 'freeze' };
const drafts = new Set(['_applyBand', '_applyCard', 'pickPersonalChoice']);
export const copy = x => JSON.parse(JSON.stringify(x));

export function firstDifference(actual, expected, at = '$') {
  if (actual === expected) return null;
  if (actual === null || expected === null || typeof actual !== 'object' || typeof expected !== 'object')
    return { path: at, actual, expected };
  if (Array.isArray(actual) !== Array.isArray(expected)) return { path: at, actual: typeof actual, expected: typeof expected };
  if (Array.isArray(actual) && actual.length !== expected.length)
    return { path: `${at}.length`, actual: actual.length, expected: expected.length };
  const ak = Object.keys(actual).sort(), ek = Object.keys(expected).sort();
  if (JSON.stringify(ak) !== JSON.stringify(ek)) return { path: `${at}.keys`, actual: ak, expected: ek };
  for (const k of Object.keys(expected)) {
    const diff = firstDifference(actual[k], expected[k], `${at}[${JSON.stringify(k)}]`);
    if (diff) return diff;
  }
  return null;
}
export function exact(actual, expected, context) {
  const diff = firstDifference(actual, expected);
  assert(!diff, `${context}: first difference ${JSON.stringify(diff)}`);
}

// Preorder seq records entry time, NOT completion time. Enforce contiguous subtrees.
export function eventTree(events) {
  const roots = [], stack = [];
  for (const [i, e] of events.entries()) {
    assert.equal(e.seq, i, `non-contiguous seq ${i}`);
    assert(e.parentSeq === null || Number.isInteger(e.parentSeq) && e.parentSeq >= 0 && e.parentSeq < i,
      `invalid parent at ${i}`);
    while (stack.length && stack.at(-1).event.seq !== e.parentSeq) stack.pop();
    assert(e.parentSeq === null || stack.length, `closed/missing parent at ${i}`);
    const node = { event: e, children: [] };
    (stack.length ? stack.at(-1).children : roots).push(node);
    stack.push(node);
    assert(stack.length <= 64, 'call tree too deep');
  }
  return roots;
}

export class History {
  constructor(budget, mode) {
    assert(['actual-primary-policy', 'observable-teammate-teacher'].includes(mode));
    this.mode = mode;
    this.prepRound = 0;
    this.lastBondState = null;
    this.steps = 0;
    this.h = { actions: [], bonds: [], pieces: {}, totals: {}, rounds: {}, byId: {}, prepSteps: 0, prepBudget: budget };
  }
  // ShopEnv.observe semantics. Only this seat's method boundary snapshots are inputs.
  observe(raw) {
    const own = raw.own, pub = { ...raw.public };
    delete pub.serverNow; delete pub.deadline;
    if (this.prepRound !== pub.round) {
      this.prepRound = pub.round; this.h.prepSteps = 0;
      for (const p of Object.values(this.h.pieces)) { p.refreshes = 0; p.boardRefreshes = 0; }
    }
    for (const p of pieces(own)) this.h.pieces[p.uid] ||= { id: p.id, firstRound: pub.round, refreshes: 0, boardRefreshes: 0 };
    const state = JSON.stringify([own.bonds, own.effects]);
    if (state !== this.lastBondState) {
      this.h.bonds.push({ round: pub.round, action: this.h.actions.length, bonds: copy(own.bonds), effects: copy(own.effects) });
      this.lastBondState = state;
    }
    return { own, public: pub, history: this.h };
  }
  finish(e, before, teacherDecision) {
    if (!e.accepted) return;
    const type = types[e.method], round = e.next_observation.public.round;
    if (type) {
      const existing = e.action.uid ? pieces(before.own).find(p => p.uid === e.action.uid) : null;
      const id = type === 'buy' ? before.own.shop.slots[e.action.slot]?.id : existing?.id;
      const h = this.h, r = h.rounds[round] ||= {};
      h.actions.push({ type, id, round, source: e.source });
      h.totals[type] = (h.totals[type] || 0) + 1; r[type] = (r[type] || 0) + 1;
      if (id && ['buy', 'sell'].includes(type)) {
        const v = h.byId[id] ||= {}, key = type === 'buy' ? 'buys' : 'sells';
        v[key] = (v[key] || 0) + 1;
        const byId = r.byId ||= {}, rv = byId[id] ||= {}; rv[key] = (rv[key] || 0) + 1;
      }
      if (type === 'refresh') for (const p of pieces(before.own)) {
        const v = h.pieces[p.uid]; v.refreshes++; if (p.area === 'board') v.boardRefreshes++;
      }
      // Teacher recorder increments before its after-observe; actual policy step increments outside the recorder.
      if (this.mode === 'observable-teammate-teacher' && teacherDecision) { this.steps++; h.prepSteps++; }
      this.observe(e.next_observation);
    } else if (drafts.has(e.method)) {
      this.h.actions.push({ type: e.method, source: e.source, round });
      this.observe(e.next_observation);
    }
    if (this.mode === 'actual-primary-policy' && e.policy) { this.steps++; this.h.prepSteps++; }
  }
}

// Invoke the ORIGINAL candidate function with its explicit observation argument.
// This read-only context contains no Match, scheduler, player methods or environment instance.
export function candidates(observation, gd) {
  return ShopEnv.prototype.candidates.call({ summary: null, m: { phase: observation.public.phase }, ps: { gd } }, observation);
}

export function bind(e, cs) {
  const type = types[e.method];
  if (!TYPES.includes(type)) return { reason: 'non-shop-method' };
  if (!e.accepted) return { reason: 'rejected' };
  if (type === 'ready' && e.action.ready !== true) return { reason: 'not-ready-true' };
  const intent = type === 'buy' ? { t: 'g.buy', slot: e.action.slot }
    : type === 'sell' ? { t: 'g.sell', uid: e.action.uid }
      : type === 'ready' ? { t: 'g.ready', ready: true } : { t: `g.${type}` };
  exact(e.action, intent, `seq=${e.seq} method/action`);
  const matches = cs.map((c, i) => [c, i]).filter(([c]) => c.type === type &&
    (type !== 'buy' || c.slot === e.action.slot) && (type !== 'sell' || c.uid === e.action.uid));
  if (matches.length !== 1) return { reason: 'no-unique-legal-candidate' };
  const [candidate, action] = matches[0];
  if (e.policy) {
    exact(candidate, e.policy.candidate, `seq=${e.seq} policy candidate all fields`);
    exact(action, e.policy.candidateIndex, `seq=${e.seq} candidate index`);
    exact(cs.length, e.policy.candidateCount, `seq=${e.seq} candidate count`);
  }
  return { candidate, action };
}

export function reduceEvents(events, { gd, schema, budget, guard = () => {}, onDecision = () => {} }) {
  const histories = new Map(), dispositions = [];
  function visit({ event: e, children }, ancestors) {
    guard();
    let h = histories.get(e.playerId);
    if (!h) histories.set(e.playerId, h = new History(budget, e.playerId === 'learner'
      ? 'actual-primary-policy' : 'observable-teammate-teacher'));
    assert(types[e.method] || drafts.has(e.method) || e.method === 'resolveTemp', `unknown method ${e.method}`);
    // resolveTemp is not wrapped by ShopEnv.installRecorder, and does not observe on its own.
    const before = e.method === 'resolveTemp' ? null : h.observe(e.observation);
    const shopping = TYPES.includes(types[e.method]);
    const teacherDecision = shopping && e.source === 'partner-controller' &&
      !ancestors.some(a => a.method === 'resolveTemp');
    const decision = !!e.policy || teacherDecision;
    let binding = { reason: shopping ? 'not-policy-or-teacher-decision' : 'non-shop-method' };
    if (!e.accepted) binding = { reason: 'rejected' };
    else if (decision) {
      const cs = candidates(before, gd); binding = bind(e, cs);
      if (!binding.reason) {
        const sample = { features: encode(before, cs, schema), action: binding.action };
        assert(sample.features.mask[binding.action] === true, `masked label ${e.seq}`);
        onDecision(e, sample, cs, binding.candidate, h);
      }
    }
    dispositions.push({ seq: e.seq, parentSeq: e.parentSeq, playerId: e.playerId, source: e.source,
      method: e.method, action: e.action, accepted: e.accepted, result: e.result,
      reason: binding.reason || 'legal-decision' });
    for (const child of children) visit(child, [...ancestors, e]);
    h.finish(e, before, teacherDecision);
  }
  for (const root of eventTree(events)) visit(root, []);
  return { histories, dispositions };
}
