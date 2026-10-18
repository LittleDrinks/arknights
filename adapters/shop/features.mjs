// Pure, versioned encoder. Its only dynamic input is the player's visible observation.
export const FEATURE_VERSION = 'shop-v1';
export const TYPES = ['buy', 'sell', 'refresh', 'levelUp', 'ready'];
const n = x => Number.isFinite(Number(x)) ? Number(x) : 0;
const scale = x => Math.sign(n(x)) * Math.log1p(Math.abs(n(x)));
const hash = s => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; };
export function makeSchema(data) {
  return { version: FEATURE_VERSION, ids: [...Object.keys(data.chess || {}), ...Object.keys(data.items || {})].sort(),
    bonds: Object.keys(data.bonds || {}).sort(), bands: Object.keys(data.bands || {}).sort() };
}
export function pieces(own) {
  return ['board', 'hand', 'temp'].flatMap(area => (own[area] || []).filter(Boolean).map(p => ({ ...p, area })));
}
// Supplemental public effects/enemy preview features; all bonds and chess identities have explicit slots below.
function sketch(value, prefix, out) {
  if (value == null) return;
  if (typeof value === 'object') {
    for (const [key, v] of Object.entries(value)) {
      if (['uid', 'ownerUid', 'playerId', 'name', 'desc', 'counterText'].includes(key)) continue;
      sketch(v, `${prefix}.${key}`, out);
    }
  } else {
    const key = typeof value === 'string' ? `${prefix}=${value}` : prefix;
    out[hash(key) % out.length] += typeof value === 'string' ? 1 : scale(value);
  }
}
export function encode(observation, candidates, schema) {
  const { own: p, public: pub, history: h } = observation;
  const owned = pieces(p), bonds = new Map(p.bonds.map(b => [b.bondId, b]));
  const round = h.rounds[String(pub.round)] || {};
  const g = [pub.round / 20, p.lp / 100, scale(p.funds), p.shop.level / 6,
    scale(p.shop.upgradePrice), scale(p.shop.refreshPrice), scale(p.shop.freeRefreshes), +!!p.shop.frozen,
    p.deployCount / 12, p.deployCap / 12, p.hand.filter(Boolean).length / p.hand.length,
    p.temp.filter(Boolean).length / 10, h.prepSteps / h.prepBudget,
    ...['NORMAL', 'HARD', 'ABYSS'].map(d => +(pub.difficulty === d)),
    ...TYPES.flatMap(t => [scale(h.totals[t]), scale(round[t])])];
  for (const id of schema.bonds) {
    const b = bonds.get(id) || {};
    g.push(scale(b.count), +!!b.active, scale(b.tier), scale(b.layers), scale(b.harmony),
      +!!b.off, +!!b.countsHand, +(pub.disabledBonds || []).includes(id));
  }
  g.push(...schema.bands.map(id => +(id === p.bandId)));
  for (const id of schema.ids) {
    const ps = owned.filter(x => x.id === id);
    g.push(...['board', 'hand', 'temp'].map(a => scale(ps.filter(x => x.area === a).length)),
      scale(ps.reduce((s, x) => s + (h.pieces[x.uid]?.refreshes || 0), 0)),
      scale(h.byId[id]?.buys), scale(h.byId[id]?.sells),
      scale(round.byId?.[id]?.buys), scale(round.byId?.[id]?.sells),
      scale(p.shop.slots.filter(s => s && !s.sold && s.id === id).length),
      ...[0, 1, 2, 3].map(k => scale(ps.filter(x => Math.min(3, h.pieces[x.uid]?.refreshes || 0) === k).length)));

  }
  const extra = new Array(64).fill(0);
  sketch({ effects: p.effects, enemies: p.nextEnemies, stats: p.stats, stage: pub.stageId,
    factions: pub.factions, boss: pub.bossId, loadout: p.loadout, ops: p.ops }, 'visible', extra);
  g.push(...extra);
  const c = candidates.map(a => {
    const piece = owned.find(x => x.uid === a.uid);
    const exposure = piece ? h.pieces[piece.uid] || {} : {};
    const bs = new Set(a.bonds || []);
    return [...TYPES.map(t => +(t === a.type)), scale(a.price), n(a.tier) / 6, +!!a.golden,
      ...['board', 'hand', 'temp'].map(area => +(piece?.area === area)),
      scale(owned.filter(x => x.id === a.id).length), scale(exposure.refreshes), scale(exposure.boardRefreshes),
      scale(pub.round - (exposure.firstRound ?? pub.round)),
      ...schema.ids.map(id => +(id === a.id)), ...schema.bonds.map(id => +bs.has(id))];
  });
  return { global: g, candidates: c, mask: candidates.map(() => true) };
}
