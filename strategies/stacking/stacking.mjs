// Teacher/control policy for Stronghold 6ab2468. No rule, RNG or state writes.
export const MODULES = ['refresh', 'acquire', 'lappland'];
const IDS = {
  aroma: 'chess_char_4_10_a', angelina: 'chess_char_5_20_a',
  dusk: 'chess_char_5_12_a', xiaoman: 'chess_char_2_04_a', lappland: 'chess_char_2_16_a',
};
const CAP = 999; // shared/constants.js at the pinned ruleset
const REFRESH = 'SERVER_ADD_REFRESH_CNT_MULTIPLIER_BOND_LAYER';
const GAIN = 'SERVER_ADD_BOND_METHOD';
const LAP = 'SERVER_GAIN_BOND_LAYER_BY_REFRESH_CNT';
const finite = (v, fallback, cap = 1e6) => Number.isFinite(v) ? Math.max(0, Math.min(cap, v)) : fallback;
const count = (v) => Number.isInteger(v) && v >= 0 ? v : null;
const owned = (ps) => [...ps.board.values(), ...ps.hand, ...ps.temp].filter(p => p?.kind === 'chess');
const history = (ps) => ps.roundStats ?? ps.round ?? {};
const snapshot = (ps, refreshes) => ({
  funds: ps.funds, freeRefreshes: ps.shop.freeRefreshes, refreshes,
  gainedChess: count(history(ps).gainedChess), spent: history(ps).spent ?? null,
  layers: { ...ps.layers }, boardUids: [...ps.board.values()].map(p => p.uid),
});

export function runStacking(m, ps, opts = {}) {
  const steps = runStackingSteps(m, ps, opts);
  let step;
  do { step = steps.next(); } while (!step.done);
  return step.value;
}

/** Yields one audited result per attempted legal action; final return is the report.
 * enabled defaults OFF. History is the current player's real roundStats/ps.round;
 * observedRefreshes is an explicit caller-maintained fallback, never an assumed zero.
 */
export function* runStackingSteps(m, ps, opts = {}) {
  const gd = ps.gd ?? m.gd;
  const enabled = Object.fromEntries(MODULES.map(k => [k, opts.enabled === true || opts.enabled?.[k] === true]));
  const maxActions = Math.floor(finite(opts.maxActions, 24, 256));
  const maxPurchases = Math.floor(finite(opts.maxPurchases, 4, 32));
  const reserve = finite(opts.reserveFunds, 6);
  const maxNetCost = finite(opts.maxNetCost, 8);
  const maxCostPerLayer = finite(opts.maxCostPerLayer, 1);
  const maxBuyPrice = finite(opts.maxBuyPrice, 2);
  let fallbackRefreshes = count(opts.observedRefreshes);
  const refreshes = () => count(history(ps).refreshes) ?? fallbackRefreshes;
  const round = m.round;
  const before = snapshot(ps, refreshes());
  const report = {
    enabled, before, after: null, attempted: 0, accepted: 0, spent: 0, credited: 0, netCost: 0,
    projectedLayerGain: 0, observedLayerDelta: {}, triggeredModules: [], actions: [], stopReason: null,
    modules: Object.fromEntries(MODULES.map(k => [k, { attempted: 0, accepted: 0, netCost: 0, stopReason: enabled[k] ? null : 'disabled' }])),
  };
  const coreIds = new Set([...Object.values(IDS), ...(opts.coreIds ?? []).map(id => gd.baseIdOf(id))]);
  const coreUids = new Set(opts.coreUids ?? []);
  let purchases = 0;
  const finish = (reason) => {
    report.stopReason = reason;
    report.after = snapshot(ps, refreshes());
    report.netCost = before.funds - ps.funds;
    for (const b of new Set([...Object.keys(before.layers), ...Object.keys(ps.layers)])) {
      report.observedLayerDelta[b] = (ps.layers[b] ?? 0) - (before.layers[b] ?? 0);
    }
    return report;
  };
  const traits = (id, effect) => (gd.chess(id)?.garrisonIds ?? []).map(id => gd.garrison(id))
    .filter(g => g?.effectKey === effect);
  const activeBonds = (g) => (g.bbStr?.bond ?? '').split(',').filter(b => ps.bonds[b]?.active);
  // Only the four supported deployed engines. A DIY record with no such trait gives no benefit.
  const prepProjection = (r, n) => {
    const out = {};
    for (const p of ps.board.values()) {
      if (p.kind !== 'chess') continue;
      const base = gd.baseIdOf(p.id);
      const effects = [IDS.aroma, IDS.angelina].includes(base) ? traits(p.id, REFRESH)
        : [IDS.dusk, IDS.xiaoman].includes(base) ? traits(p.id, GAIN) : [];
      for (const g of effects) {
        const value = g.effectKey === REFRESH
          ? Math.min((r ?? 0) * g.bb.multiplier, g.bb.max_layer)
          : g.bbStr.add_method === 'round_gain_char' ? n * g.bb.multi : 0;
        for (const b of activeBonds(g)) out[b] = (out[b] ?? 0) + value;
      }
    }
    return out;
  };
  // Marginal *deferred* layers, conditional on keeping these deployed engines through prep end.
  const benefit = (extraRefresh = 0, extraGain = 0, direct = {}) => {
    const r = refreshes();
    const n = count(history(ps).gainedChess) ?? 0;
    const a = prepProjection(r, n), b = prepProjection(r == null ? null : r + extraRefresh, n + extraGain);
    let gain = 0;
    for (const bond of new Set([...Object.keys(a), ...Object.keys(b), ...Object.keys(direct)])) {
      const room = Math.max(0, CAP - (ps.layers[bond] ?? 0));
      gain += Math.max(0, Math.min(room, (b[bond] ?? 0) + (direct[bond] ?? 0)) - Math.min(room, a[bond] ?? 0));
    }
    return gain;
  };
  const refreshPrice = () => ps.shop.freeRefreshes > 0 ? 0 : gd.refreshPrice;
  const guard = (cost = 0, actions = 1) => {
    if (report.stopReason) return report.stopReason;
    if (m.phase !== 'PREP' || m.round !== round || !ps.alive || ps.ready) return 'not-actionable';
    if (report.attempted + actions > maxActions) return 'action-budget';
    if (cost > 0 && ps.funds - cost < reserve) return 'reserve-funds';
    if (Math.max(0, before.funds - ps.funds) + cost > maxNetCost) return 'net-cost-budget';
    return null;
  };
  const affordable = (cost, gain, actions = 1) => guard(cost, actions)
    ?? (!(gain > 0) ? 'no-marginal-layers' : cost / gain > maxCostPerLayer ? 'cost-per-layer' : null);
  function* act(module, action, arg, projectedGain = 0) {
    const reason = guard(action === 'buy' ? ps.priceOf(ps.shop.slots[arg]) : action === 'refresh' ? refreshPrice() : 0);
    if (reason) { report.stopReason = reason; return false; }
    const start = snapshot(ps, refreshes());
    report.attempted++; report.modules[module].attempted++;
    if (!report.triggeredModules.includes(module)) report.triggeredModules.push(module);
    let result;
    try { result = ps[action](arg); }
    catch (e) { result = { error: 'exception', detail: String(e.message ?? e) }; }
    const ok = result?.ok === true;
    if (ok && action === 'refresh' && fallbackRefreshes != null) fallbackRefreshes++;
    if (ok && action === 'buy') purchases++;
    const end = snapshot(ps, refreshes());
    const spent = start.spent != null && end.spent != null ? Math.max(0, end.spent - start.spent)
      : Math.max(0, start.funds - end.funds);
    const event = { module, action, ...(arg != null ? { [action === 'buy' ? 'slot' : 'uid']: arg } : {}),
      accepted: ok, result, before: start, after: end, spent, netCost: start.funds - end.funds };
    report.spent += spent;
    report.credited += end.funds - start.funds + spent;
    report.modules[module].netCost += event.netCost;
    if (ok) {
      report.accepted++; report.modules[module].accepted++;
      report.projectedLayerGain += projectedGain;
    } else report.stopReason = `action-failed:${result?.error ?? 'invalid-result'}`;
    report.actions.push(event);
    yield event;
    return ok;
  }
  // Exact new plain identity only. A merge/transformation (even if it creates just one new UID) is never disposable.
  const freshPlain = (prior, id) => {
    const now = owned(ps);
    if (prior.some(old => !now.some(p => p.uid === old.uid && p.id === old.id))) return null;
    const added = now.filter(p => !prior.some(old => old.uid === p.uid));
    if (added.length !== 1) return null;
    const p = added[0];
    return p.id === id && !gd.isGolden(p.id) && !p.items?.length && ps.hand.includes(p) ? p : null;
  };
  const canSell = (p, prior, id, lap = false) => {
    if (!p || freshPlain(prior, id)?.uid !== p.uid || coreUids.has(p.uid)) return false;
    const base = gd.baseIdOf(id);
    // The Lappland module explicitly permits cycling a new spare while preserving the original core.
    if (lap) return prior.some(old => gd.baseIdOf(old.id) === IDS.lappland)
      && !(opts.coreIds ?? []).some(id => gd.baseIdOf(id) === base);
    return !coreIds.has(base) && !prior.some(old => gd.baseIdOf(old.id) === base);
  };
  const offers = (lap = false) => ps.shop.slots.map((s, slot) => ({ s, slot }))
    .filter(({ s }) => s?.kind === 'chess' && !s.sold && !gd.isGolden(s.id)
      && (lap ? gd.baseIdOf(s.id) === IDS.lappland : true))
    .map(({ s, slot }) => ({ id: s.id, slot, price: ps.priceOf(s) }))
    .filter(o => Number.isFinite(o.price) && o.price <= maxBuyPrice)
    .sort((a, b) => a.price - b.price || a.slot - b.slot);

  if (!Object.values(enabled).some(Boolean)) return finish('disabled');
  if (guard()) return finish(guard());
  // Use the currently visible Lappland offer before a refresh replaces it. Never search unseen shops for it.
  if (enabled.lappland) {
    let reason = 'no-cheap-lappland';
    while (purchases < maxPurchases && report.attempted < maxActions) {
      if (!ps.bonds.siracusaShip?.active) { reason = 'inactive-bond'; break; }
      if (!ps.hand.includes(null)) { reason = 'hand-full'; break; }
      const offer = offers(true)[0];
      if (!offer) break;
      const prior = owned(ps).map(p => ({ uid: p.uid, id: p.id }));
      const same = prior.filter(p => gd.baseIdOf(p.id) === IDS.lappland && !gd.isGolden(p.id));
      // Do not consume the core/pair merely to refresh an elite. The regular bot can make this merge.
      if (same.length + 1 >= gd.mergeCount(offer.id)) { reason = 'merge-risk'; break; }
      const direct = {};
      for (const g of traits(offer.id, LAP)) for (const b of activeBonds(g)) direct[b] = (direct[b] ?? 0) + g.bb.layer;
      const gain = benefit(1, 1, direct);
      // Reserve the entire buy + refresh + optional sale action sequence. Refunds are NOT assumed income.
      reason = affordable(offer.price + refreshPrice(), gain, 3);
      if (reason) break;
      const witnessedBeforeBuy = refreshes();
      if (!(yield* act('lappland', 'buy', offer.slot))) break;
      const fresh = freshPlain(prior, offer.id);
      if (!fresh) { reason = 'identity-changed-retained'; break; }
      // Only a copy just acquired by this macro is known not to have witnessed a refresh.
      // A caller may interleave another action between generator steps; never refresh it twice.
      if (refreshes() !== witnessedBeforeBuy) { reason = 'refresh-history-changed-retained'; break; }
      if (!ps.bonds.siracusaShip?.active) { reason = 'inactive-bond'; break; }
      if (!(yield* act('lappland', 'refresh', undefined, gain))) break;
      if (opts.sellNew !== false && canSell(fresh, prior, offer.id, true)) {
        if (!(yield* act('lappland', 'sell', fresh.uid))) break;
      }
      reason = 'no-cheap-lappland';
    }
    report.modules.lappland.stopReason = report.stopReason ?? (purchases >= maxPurchases ? 'purchase-budget'
      : report.attempted >= maxActions ? 'action-budget' : reason);
  }
  if (report.stopReason) return finish(report.stopReason);
  if (enabled.refresh) {
    let reason;
    while (report.attempted < maxActions) {
      const n = refreshes();
      if (n == null) { reason = 'unknown-refresh-history'; break; }
      if (n >= 3) { reason = 'refresh-cap'; break; }
      const gain = benefit(1);
      reason = affordable(refreshPrice(), gain);
      if (reason) break;
      if (!(yield* act('refresh', 'refresh', undefined, gain))) break;
    }
    report.modules.refresh.stopReason = report.stopReason ?? reason ?? 'action-budget';
  }
  if (report.stopReason) return finish(report.stopReason);
  if (enabled.acquire) {
    let reason = 'no-cheap-offer';
    while (purchases < maxPurchases && report.attempted < maxActions) {
      if (!ps.hand.includes(null)) { reason = 'hand-full'; break; }
      const gain = benefit(0, 1);
      if (!(gain > 0)) { reason = 'no-marginal-layers'; break; }
      const offer = offers()[0];
      if (!offer) break;
      reason = affordable(offer.price, gain, opts.sellNew === false ? 1 : 2);
      if (reason) break;
      const prior = owned(ps).map(p => ({ uid: p.uid, id: p.id }));
      if (!(yield* act('acquire', 'buy', offer.slot, gain))) break;
      const fresh = freshPlain(prior, offer.id);
      if (!fresh) { reason = 'identity-changed-retained'; break; }
      if (opts.sellNew !== false && canSell(fresh, prior, offer.id)) {
        if (!(yield* act('acquire', 'sell', fresh.uid))) break;
      }
      reason = 'no-cheap-offer';
    }
    report.modules.acquire.stopReason = report.stopReason ?? (purchases >= maxPurchases ? 'purchase-budget'
      : report.attempted >= maxActions ? 'action-budget' : reason);
  }
  return finish(report.stopReason ?? (report.attempted >= maxActions ? 'action-budget' : 'complete'));
}
