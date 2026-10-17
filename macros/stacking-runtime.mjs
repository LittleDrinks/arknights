import { MODULES, runStackingSteps } from './stacking.mjs';

// Module-owned accounting only: never attaches fields to PlayerState/Match. One bounded report pair per seat.
const runs = new WeakMap();
export function stackingReports(ps) { return runs.get(ps)?.reports ?? []; }

export function environmentOptions(env = process.env) {
  const value = (env.ARK_STACKING ?? '').trim().toLowerCase();
  const names = ['1', 'all', 'true'].includes(value) ? MODULES
    : ['', '0', 'off', 'false'].includes(value) ? [] : value.split(',');
  if (names.some(k => !MODULES.includes(k))) throw new Error(`Unknown ARK_STACKING module: ${value}`);
  const config = JSON.parse(env.ARK_STACKING_OPTIONS ?? '{}');
  if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('ARK_STACKING_OPTIONS must be an object');
  return { ...config, enabled: Object.fromEntries(MODULES.map(k => [k, names.includes(k)])) };
}

export function* stackingPrepSteps(m, ps, phase) {
  const opts = environmentOptions();
  if (!Object.values(opts.enabled).some(Boolean)) return null;
  let state = runs.get(ps);
  if (!state || state.round !== m.round) {
    state = { round: m.round, reports: [], attempted: 0, purchases: 0, cost: 0, failed: false };
    runs.set(ps, state);
  }
  if (state.failed || state.reports.some(r => r.phase === phase)) return null;
  if (phase === 'tail' && process.env.ARK_STACKING_TAIL !== '1') return null;
  const remaining = (value, fallback, used, cap) => Math.max(0, Math.min(cap,
    Number.isFinite(value) ? Math.max(0, value) : fallback) - used);
  const report = yield* runStackingSteps(m, ps, {
    ...opts,
    maxActions: remaining(opts.maxActions, 24, state.attempted, 256),
    maxPurchases: remaining(opts.maxPurchases, 4, state.purchases, 32),
    maxNetCost: remaining(opts.maxNetCost, 8, state.cost, 1e6),
  });
  state.attempted += report.attempted;
  state.purchases += report.actions.filter(a => a.accepted && a.action === 'buy').length;
  state.cost += Math.max(0, report.netCost);
  state.failed = report.stopReason.startsWith('action-failed:');
  state.reports.push({ phase, ...report });
  return report;
}
