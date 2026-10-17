// Heuristic teacher for later distillation, NOT a learned model.
// Reads own visible state/static records only; all real actions use PlayerState methods.
import { runStackingSteps } from '../macros/stacking.mjs';

export const IDS = Object.freeze({ aroma: 'chess_char_4_10_a', angelina: 'chess_char_5_20_a',
  lappland: 'chess_char_2_16_a', dusk: 'chess_char_5_12_a', xiaoman: 'chess_char_2_04_a' });
export const VARIANTS = Object.freeze({ refresh: ['aroma', 'angelina', 'lappland'],
  acquire: ['dusk', 'xiaoman'], mixed: Object.keys(IDS) });
export const BUDGETS = Object.freeze({
  conservative: { setupCost: 8, setupBuys: 3, reserveFunds: 4, maxActions: 12, maxPurchases: 2, maxNetCost: 4 },
  aggressive: { setupCost: 16, setupBuys: 6, reserveFunds: 0, maxActions: 24, maxPurchases: 4, maxNetCost: 8 },
});
export function options(env = process.env) {
  const variant = (env.ARK_TEACHER ?? 'off').trim().toLowerCase();
  const budget = env.ARK_TEACHER_BUDGET ?? 'conservative';
  if (!['off', '0', 'false', '', ...Object.keys(VARIANTS)].includes(variant)) throw new Error(`Unknown teacher variant: ${variant}`);
  if (!Object.hasOwn(BUDGETS, budget)) throw new Error(`Unknown teacher budget: ${budget}`);
  return { variant, budget, on: Object.hasOwn(VARIANTS, variant), ...BUDGETS[budget] };
}
const states = new WeakMap();
const owned = ps => [...ps.board.values(), ...ps.hand, ...ps.temp].filter(p => p?.kind === 'chess');
const bases = ps => new Set(owned(ps).map(p => ps.gd.baseIdOf(p.id)));
const coreIds = o => (VARIANTS[o.variant] ?? []).map(k => IDS[k]);
const targets = o => o.variant === 'refresh' ? ['siracusaShip', 'arcaneShip']
  : o.variant === 'acquire' ? ['yanShip', 'arcaneShip'] : ['siracusaShip', 'arcaneShip', 'yanShip'];
function state(m, ps) {
  let s = states.get(ps);
  if (!s || s.round !== m.round) {
    s = { round: m.round, setupCost: 0, setupBuys: 0, attempted: 0, purchases: 0, cost: 0,
      failed: false, phases: [], buys: [], macros: [] };
    states.set(ps, s);
  }
  return s;
}
export const reports = ps => states.get(ps) ?? null;
const actionable = (m, ps) => m.phase === 'PREP' && ps.alive && !ps.ready;
const threshold = (ps, id) => ps.gd.bond(id)?.thresholds?.[0] ?? ps.gd.bond(id)?.activeCount ?? 2;

// Only the first distinct supporters needed to reach each activation threshold are bought.
// No unseen shop sampling, pool reads, RNG, or hypothetical state dispatched to the game.
export function priority(ps, id, o = options()) {
  if (!o.on) return 0;
  const base = ps.gd.baseIdOf(id), have = bases(ps), c = ps.gd.chess(id);
  if (!c) return 0;
  if (coreIds(o).includes(base)) {
    const same = owned(ps).filter(p => ps.gd.baseIdOf(p.id) === base);
    if (same.some(p => ps.gd.isGolden(p.id))) return 0;
    return same.length ? 45 : 100;
  }
  if (have.has(base)) return 0;
  let value = 0;
  for (const b of targets(o)) {
    if (ps.gd.modeInactiveBonds.has(b) || !c.bonds?.includes(b)) continue;
    const n = [...have].filter(id => ps.gd.chess(id)?.bonds?.includes(b)).length;
    if (n < threshold(ps, b)) value += 32 + n * 6;
  }
  return value;
}

export function protectedPiece(ps, p, o = options()) {
  if (!o.on || p?.kind !== 'chess') return false;
  if (p.items?.length || coreIds(o).includes(ps.gd.baseIdOf(p.id))) return true;
  // Protect one representative of each required supporter; extras remain sellable.
  for (const b of targets(o)) {
    const seen = new Set();
    const supporters = owned(ps).filter(q => ps.gd.chess(q.id)?.bonds?.includes(b))
      .sort((a, z) => Number(coreIds(o).includes(ps.gd.baseIdOf(z.id))) - Number(coreIds(o).includes(ps.gd.baseIdOf(a.id)))
        || Number(ps.gd.isGolden(z.id)) - Number(ps.gd.isGolden(a.id)) || a.uid - z.uid);
    for (const q of supporters) {
      const base = ps.gd.baseIdOf(q.id);
      if (seen.has(base)) continue;
      seen.add(base);
      if (seen.size > threshold(ps, b)) break;
      if (q.uid === p.uid) return true;
    }
  }
  return false;
}
export function safeSell(ps, uid) {
  if (protectedPiece(ps, ps.find(uid)?.piece)) return { error: 'teacher-protected' };
  return ps.sell(uid);
}
export function lineupBonus(ps, set, bonds, o = options()) {
  if (!o.on) return 0;
  const distinct = new Set(set.map(p => ps.gd.baseIdOf(p.id)));
  let score = coreIds(o).filter(id => distinct.has(id)).length * 48;
  for (const id of targets(o)) {
    if (ps.gd.modeInactiveBonds.has(id)) continue;
    const b = bonds[id];
    score += Math.min(b?.count ?? 0, threshold(ps, id)) * 14 + (b?.active ? 65 : 0);
  }
  return score;
}

export function* buyVisibleSteps(m, ps) {
  const o = options();
  if (!o.on || !actionable(m, ps)) return;
  const s = state(m, ps);
  // Shared across all native buy-loop visits in this prep, including merges and re-entry.
  while (!s.failed && s.setupBuys < o.setupBuys && ps.hand.includes(null) && actionable(m, ps) && m.round === s.round) {
    const candidates = ps.shop.slots.map((slot, index) => ({ slot, index }))
      .filter(({ slot }) => slot?.kind === 'chess' && !slot.sold)
      .map(x => ({ ...x, price: ps.priceOf(x.slot), score: priority(ps, x.slot.id, o) }))
      .filter(x => x.score > 0 && Number.isFinite(x.price) && x.price >= 0
        && s.setupCost + x.price <= o.setupCost && ps.funds - x.price >= o.reserveFunds)
      .sort((a, b) => b.score - a.score || a.price - b.price || a.index - b.index);
    const c = candidates[0];
    if (!c) break;
    const before = ps.funds;
    const result = ps.buy(c.index);
    const event = { slot: c.index, id: c.slot.id, price: c.price, fundsBefore: before, fundsAfter: ps.funds, result };
    s.buys.push(event);
    // Count gross paid price, never assume sell refunds or onBuy gifts are free budget.
    if (result?.ok) { s.setupCost += c.price; s.setupBuys++; } else s.failed = true;
    yield event;
  }
}

export function* macroSteps(m, ps, phase) {
  const o = options();
  if (!o.on || !actionable(m, ps)) return;
  const s = state(m, ps);
  if (s.failed || s.phases.includes(phase)) return;
  s.phases.push(phase);
  const enabled = { refresh: o.variant !== 'acquire', lappland: o.variant !== 'acquire', acquire: o.variant !== 'refresh' };
  const report = yield* runStackingSteps(m, ps, { ...o, enabled,
    maxActions: Math.max(0, o.maxActions - s.attempted),
    maxPurchases: Math.max(0, o.maxPurchases - s.purchases),
    maxNetCost: Math.max(0, o.maxNetCost - s.cost),
    maxBuyPrice: 3, maxCostPerLayer: 1,
    // Protect all existing identities, supporter bases and equipment; new spare Lappland may cycle.
    coreUids: owned(ps).map(p => p.uid),
    coreIds: owned(ps).filter(p => protectedPiece(ps, p, o) && ps.gd.baseIdOf(p.id) !== IDS.lappland).map(p => p.id),
  });
  s.attempted += report.attempted;
  s.purchases += report.actions.filter(a => a.accepted && a.action === 'buy').length;
  s.cost += Math.max(0, report.netCost);
  s.failed ||= report.stopReason.startsWith('action-failed:');
  s.macros.push({ phase, ...report });
}
