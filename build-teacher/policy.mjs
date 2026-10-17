// Guide-informed candidate heuristic v1, NOT a trained/strong model.
// Only Match read: the public disabledBonds list. No shared-pool, RNG, seed,
// wave-generation or simulation access here.
export function options(env = process.env) {
  const build = env.ARK_BUILD_TEACHER ?? 'off';
  if (!['off', 'sargon', 'kjerag'].includes(build)) throw new Error('build-teacher: invalid build');
  const toggle = key => {
    const v = env[key] ?? '1';
    if (!['0', '1'].includes(v)) throw new Error(`build-teacher: invalid ${key}`);
    return build !== 'off' && v === '1';
  };
  return Object.freeze({ build, on: build !== 'off', roster: toggle('ARK_BUILD_ROSTER'),
    equipment: toggle('ARK_BUILD_EQUIPMENT'), adjacency: toggle('ARK_BUILD_ADJACENCY') });
}
export const CONFIG = options();
export const BUILDS = Object.freeze({
  sargon: { bond: 'sargonShip', names: ['至简', '缇缇', '异客', '佩佩', '百炼嘉维尔', '卡涅利安', '莱恩哈特', '空弦'],
    engines: ['至简', '缇缇', '莱恩哈特'], side: ['preciShip', 'swiftShip', 'skillfulShip'] },
  kjerag: { bond: 'kjeragShip', names: ['角峰', '初雪', '灵知', '凛御银灰', '圣聆初雪', '锏', '空弦', '夕'],
    engines: ['初雪', '灵知', '空弦'], side: ['skillfulShip', 'arcaneShip', 'swiftShip'] },
});
export const ITEMS = Object.freeze({ compass: 'chess_item_6_07_e', tea: 'chess_item_2_04_e',
  arm: 'chess_item_5_03_e', tears: 'chess_item_6_06_e', ice: 'chess_item_5_02_e' });
const family = id => String(id).replace(/_[ab]$/, '');
const name = (ps, p) => ps.gd.chess(p.id)?.name;
const board = ps => [...ps.board.values()].filter(p => p.kind === 'chess');
const isHeal = c => c && (c.dmgType === 'heal' || c.attackKind === 'heal');
const isBlock = c => c && c.position === 'MELEE' && (c.stats?.blockCnt ?? 1) > 0 && c.attackKind !== 'none';
const memo = new WeakMap();
function note(ps, kind, detail = {}) {
  let r = memo.get(ps);
  if (!r) { r = { counts: {}, events: [], truncated: 0 }; memo.set(ps, r); }
  r.counts[kind] = (r.counts[kind] ?? 0) + 1;
  if (r.events.length < 256) r.events.push({ kind, ...detail }); else r.truncated++;
}
export const reports = ps => memo.get(ps) ?? { counts: {}, events: [], truncated: 0 };
// Explicit config argument is useful to non-loader callers and contract tests.
export function strategy(ps, cfg = CONFIG) {
  const b = BUILDS[cfg.build];
  return cfg.on && b && !ps.gd.modeInactiveBonds.has(b.bond) && !ps.m?.disabledBonds?.includes(b.bond) ? b : null;
}
export function focusBonus(ps, id, cfg = CONFIG) {
  return cfg.roster && strategy(ps, cfg)?.bond === id ? 18 : 0;
}
export function buyBonus(ps, id, cfg = CONFIG) {
  const b = strategy(ps, cfg), c = ps.gd.chess(id);
  if (!cfg.roster || !b || !c || !c.visible || c.isDiy) return 0;
  const all = ps.allChess(), base = ps.gd.baseIdOf(id);
  const copies = all.filter(p => ps.gd.baseIdOf(p.id) === base);
  const member = c.bonds.includes(b.bond), core = b.names.includes(c.name);
  // Do not hoard another normal cycle after the elite is already owned.
  if (copies.some(p => ps.gd.isGolden(p.id))) return core || member ? -12 : 0;
  if (copies.length) return core ? 9 : member && copies.length >= 2 ? 4 : 0;
  const ownedMembers = new Set(all.filter(p => ps.gd.chess(p.id)?.bonds.includes(b.bond))
    .map(p => ps.gd.baseIdOf(p.id))).size;
  let s = core ? 12 : 0;
  if (b.engines.includes(c.name)) s += 5;
  if (member && ownedMembers < 6) s += ownedMembers < 3 ? 16 : 10;
  // Cheap *visible* distinct fillers get a bounded bias; no purchase/reroll macro.
  if (member && ownedMembers < 3 && c.tier <= 3) s += 4;
  if (isHeal(c) && !all.some(p => isHeal(ps.gd.chess(p.id)))) s += 10;
  return s;
}
export function lineupBonus(ps, set, bonds, cfg = CONFIG) {
  const b = strategy(ps, cfg);
  if (!cfg.roster || !b) return 0;
  const distinct = new Map(set.map(p => [ps.gd.baseIdOf(p.id), p]));
  let s = Math.min(8, set.length) * 3;
  const core = bonds[b.bond];
  s += Math.min(6, core?.count ?? 0) * 6 + (core?.active ? 16 : 0) + ((core?.count ?? 0) >= 6 ? 18 : 0);
  for (const p of distinct.values()) {
    const n = name(ps, p);
    if (b.names.includes(n)) s += 6;
    if (b.engines.includes(n)) s += 5;
  }
  for (const id of b.side) if (bonds[id]?.active) s += 5;
  const owned = ps.allChess().map(p => ps.gd.chess(p.id));
  const chosen = set.map(p => ps.gd.chess(p.id));
  if (owned.some(isHeal) && !chosen.some(isHeal)) s -= 30;
  if (owned.some(c => c?.canHitFly && !isHeal(c)) && !chosen.some(c => c?.canHitFly && !isHeal(c))) s -= 18;
  s -= Math.max(0, Math.min(2, owned.filter(isBlock).length) - chosen.filter(isBlock).length) * 10;
  return s;
}
// Priority is bounded, not an irrevocable UID lock. Emergency overflow may legally sell.
export function protection(ps, p, cfg = CONFIG) {
  const b = strategy(ps, cfg);
  if (!cfg.roster || !b || !p || p.kind !== 'chess') return 0;
  const all = ps.allChess(), base = ps.gd.baseIdOf(p.id), c = ps.gd.chess(p.id);
  const same = all.filter(q => ps.gd.baseIdOf(q.id) === base).sort((a, z) =>
    Number(ps.gd.isGolden(z.id)) - Number(ps.gd.isGolden(a.id)) || (z.items?.length ?? 0) - (a.items?.length ?? 0) || a.uid - z.uid);
  const first = same[0]?.uid === p.uid;
  if (!first) return b.names.includes(c?.name) && !same.some(q => ps.gd.isGolden(q.id)) && same.indexOf(p) < 2 ? 12 : 0;
  if (b.names.includes(c?.name)) return b.engines.includes(c.name) ? 32 : 24;
  if (ps.find(p.uid)?.area === 'board') {
    if ((p.items?.length ?? 0) > 0) return 14;
    if (c?.bonds.includes(b.bond) && (ps.bonds[b.bond]?.count ?? 0) <= 6) return 16;
    if (isHeal(c) && board(ps).filter(q => isHeal(ps.gd.chess(q.id))).length === 1) return 18;
  }
  return 0;
}
export function safeSell(ps, uid, cfg = CONFIG) {
  const p = ps.find(uid)?.piece;
  if (protection(ps, p, cfg)) {
    note(ps, 'sale-refused', { uid, id: p.id });
    return { ok: false, error: 'BUILD_PROTECTED' };
  }
  return ps.sell(uid);
}
// Called only at the native bot's final overflow fallback. Never discard a chess.
export function finishTemp(ps, cfg = CONFIG) {
  if (!cfg.roster || !strategy(ps, cfg)) { if (!ps.tempEmpty) ps.resolveTemp(); return; }
  for (let n = 0; n < 128 && ps.temp.some(p => p?.kind === 'chess'); n++) {
    const p = ps.temp.find(p => p?.kind === 'chess');
    const idx = ps.hand.indexOf(null);
    if (idx >= 0 && ps.move(p.uid, { area: 'hand', idx }).ok) continue;
    const candidates = [...ps.hand, ...ps.temp].filter(q => q?.kind === 'chess')
      .sort((a, b) => protection(ps, a, cfg) - protection(ps, b, cfg)
        || (ps.gd.chess(a.id)?.tier ?? 0) - (ps.gd.chess(b.id)?.tier ?? 0) || a.uid - b.uid);
    const q = candidates[0];
    const before = ps.funds, priority = protection(ps, q, cfg), id = q.id;
    const result = ps.sell(q.uid); // the sole emergency bypass; actual refund, pool return, equipment return
    note(ps, 'overflow-sale', { uid: q.uid, id, priority, ok: result.ok, refund: ps.funds - before });
    if (!result.ok) throw new Error('build-teacher: overflow legal sale refused');
  }
  if (ps.temp.some(p => p?.kind === 'chess')) throw new Error('build-teacher: overflow guard exhausted');
  if (!ps.tempEmpty) ps.resolveTemp(); // tokens/items only
}
function desired(ps, p, cfg) {
  const n = name(ps, p);
  if (cfg.build === 'sargon') {
    if (n === '至简') return [ITEMS.compass, ITEMS.tea];
    if (['异客', '佩佩'].includes(n)) return [ITEMS.arm, ITEMS.tea];
  } else if (cfg.build === 'kjerag') {
    if (n === '圣聆初雪') return [ITEMS.arm, ITEMS.tears];
    if (n === '空弦') return [ITEMS.ice, ITEMS.tea];
    if (n === '锏') return [ITEMS.arm, ITEMS.tea];
  }
  return [];
}
export function reserved(ps, p, item, cfg = CONFIG) {
  if (!cfg.equipment || !strategy(ps, cfg)) return false;
  const want = desired(ps, p, cfg);
  return want.length > 0 && (!want.includes(family(item.id)) || p.items.some(i => family(i.id) === family(item.id)));
}
export function equipmentTarget(ps, item, cfg = CONFIG) {
  if (!cfg.equipment || !strategy(ps, cfg) || !Object.values(ITEMS).includes(family(item.id))) return undefined;
  const candidates = board(ps).filter(p => (p.items?.length ?? 0) < ps.gd.equipPerChess
    && desired(ps, p, cfg).includes(family(item.id)) && !p.items.some(i => family(i.id) === family(item.id)));
  candidates.sort((a, b) => {
    const score = p => (['至简', '圣聆初雪'].includes(name(ps, p)) ? 20 : 0)
      + p.items.filter(i => desired(ps, p, cfg).includes(family(i.id))).length * 12;
    return score(b) - score(a) || a.uid - b.uid;
  });
  return candidates[0]; // no desired owned carrier: retain the original item's general scoring
}
export function itemBonus(ps, id, cfg = CONFIG) {
  if (!cfg.equipment || !strategy(ps, cfg)) return 0;
  const target = equipmentTarget(ps, { id }, cfg);
  return target ? 16 : 0;
}
export function cardBonus(ps, card, cfg = CONFIG) {
  return card?.kind === 'item' ? itemBonus(ps, card.id, cfg) : 0;
}
export function placementBonus(ps, p, key, plan, pieces, value, cover, cfg = CONFIG) {
  if (!cfg.adjacency || cfg.build !== 'kjerag' || !strategy(ps, cfg) || !ps.bonds.skillfulShip?.active || !cover.size) return 0;
  const n = name(ps, p), other = n === '圣聆初雪' ? '灵知' : n === '灵知' ? '圣聆初雪' : null;
  if (!other) return 0;
  const q = pieces.find(x => name(ps, x) === other && plan.has(x.uid));
  if (!q) return 0;
  const [r, c] = key.split(',').map(Number), [r1, c1] = plan.get(q.uid).split(',').map(Number);
  const dr = Math.abs(r-r1), dc = Math.abs(c-c1);
  const near = (ps.layers.skillfulShip ?? 0) >= 40 ? Math.max(dr, dc) === 1 : dr + dc === 1;
  return near ? Math.abs(value) * 0.04 : 0; // bounded 4% bias in original legal tile/direction search
}
