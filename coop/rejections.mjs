import { serialize } from 'node:v8';

// Audited against the pinned player/{economy,placement,prep,items}.js. Codes alone
// are insufficient: item handlers can return the same errors AFTER dispatching effects.
const validation = {
  buy: ['BAD_TARGET', 'SOLD_OUT', 'NO_FUNDS', 'HAND_FULL'],
  sell: ['BAD_TARGET'], refresh: ['NO_FUNDS'], levelUp: ['MAX_LEVEL', 'NO_FUNDS'],
  setReady: ['BAD_TARGET', 'TEMP_NOT_EMPTY'], move: ['BAD_TARGET', 'BAD_TILE', 'BOARD_FULL', 'HAND_FULL'],
  equip: ['BAD_TARGET'], pickReward: ['BAD_TARGET', 'SOLD_OUT', 'HAND_FULL', 'NO_FUNDS'],
  useArt: ['BAD_TARGET', 'BAD_TILE'], destroy: ['BAD_TARGET'], freeze: [],
};

// Internal guard only, never an observation. Include hidden counters, Map order,
// stock/debt, uid allocation, RNG and scheduler state, not just privateView().
// Exclude immutable rule references, method wrappers and lazy deploy-map caches.
export function rejectionState(m) {
  const players = m.order.map(p => Object.fromEntries(Object.entries(p).filter(([k, v]) =>
    !['m', 'gd', '_deployMap', '_deployField'].includes(k) && typeof v !== 'function')));
  return serialize({ players, pool: m.pool.entries, itemDebt: m.itemPool.debt, uid: m.uidSeq,
    phase: m.phase, round: m.round, publicDirty: m._pubDirty, privateDirty: [...m._privDirty].map(p => p.playerId),
    rng: Object.fromEntries(Object.entries(m).filter(([k]) => k.startsWith('rng')).map(([k, r]) => [k, r.state()])),
    scheduler: { seq: m.sched._seq, time: m.sched.t,
      queue: m.sched._q.map(({ fn, ...entry }) => entry) } });
}

export function checkedProposal(ps, method, invoke, { arrange = false } = {}) {
  const m = ps.m, before = rejectionState(m), dispatch = m.dispatchItem;
  let effectDispatched = false;
  m.dispatchItem = function (...args) { effectDispatched = true; return dispatch.apply(this, args); };
  let result;
  try { result = invoke(); } finally { m.dispatchItem = dispatch; }
  if (result?.ok) return { result };
  const unchanged = before.equals(rejectionState(m));
  const known = arrange ? method === 'move' && ['BAD_TILE', 'BOARD_FULL'].includes(result?.error) :
    Object.hasOwn(validation, method) && [...validation[method], 'WRONG_PHASE', 'ELIMINATED'].includes(result?.error);
  return { result, diagnostic: known && unchanged && !effectDispatched,
    reason: effectDispatched ? 'effect-dispatched' : !unchanged ? 'state-changed' : known ? 'pure-validation' : 'unknown-rejection' };
}
