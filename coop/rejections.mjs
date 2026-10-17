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
  // Capture Match fields by default, including newly introduced scheduling latches.
  // Static rules/services and player references are represented by stable tags;
  // their mutable counterparts are captured separately below. Functions have
  // stable identities so replacing a queued callback is detectable as well.
  const refs = new Map([[m, 'match'], [m.gd, 'rules'], [m.ds, 'data-source'],
    [m.registry, 'registry'], [m.dispatcher, 'dispatcher'], [m.stage, 'stage'],
    [m.opts?.data, 'static-data'], ...m.order.map(p => [p, 'player:' + p.playerId])]);
  const seen = new Map();
  const project = value => {
    if (typeof value === 'function') {
      if (!functionIds.has(value)) functionIds.set(value, ++functionSequence);
      return ['function', functionIds.get(value)];
    }
    if (value === null || typeof value !== 'object') return value;
    if (refs.has(value)) return ['reference', refs.get(value)];
    if (seen.has(value)) return ['cycle', seen.get(value)];
    seen.set(value, seen.size);
    if (Array.isArray(value)) return value.map(project);
    if (value instanceof Map) return ['map', [...value].map(([k,v]) => [project(k), project(v)])];
    if (value instanceof Set) return ['set', [...value].map(project)];
    return ['object', value.constructor?.name, Object.entries(value).map(([k,v]) => [k, project(v)])];
  };
  const players = m.order.map(p => Object.fromEntries(Object.entries(p).filter(([k, v]) =>
    !['m', 'gd', '_deployMap', '_deployField'].includes(k) && typeof v !== 'function').map(([k,v]) => [k, project(v)])));
  const excluded = new Set(['opts','log','gd','ds','registry','dispatcher','stage','players','order','pool','itemPool','sched']);
  const match = Object.fromEntries(Object.entries(m).filter(([k,v]) => !excluded.has(k) && typeof v !== 'function')
    .map(([k,v]) => [k, project(v)]));
  return serialize({ players, match, pool: project(m.pool), itemPool: project(m.itemPool),
    rng: Object.fromEntries(Object.entries(m).filter(([k]) => k.startsWith('rng')).map(([k, r]) => [k, r.state()])),
    scheduler: { seq: m.sched._seq, time: m.sched.t,
      queue: project(m.sched._q) } });
}
const functionIds = new WeakMap();
let functionSequence = 0;

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
