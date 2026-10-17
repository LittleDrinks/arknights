// Weak hand-written heuristic. No bot scoring, pool, game reference, snapshot, or RNG.
import { pieces } from '../rl/features.mjs';
export const TEACHER_VERSION = 'weak-visible-heuristic/v1';
const CORE = new Set(['chess_char_4_10_a', 'chess_char_5_20_a', 'chess_char_2_16_a',
  'chess_char_5_12_a', 'chess_char_2_04_a']);

// Copy only static fields used by this policy; never pass GameData/Match to query().
export function staticRecords(data) {
  return {
    chess: Object.fromEntries(Object.entries(data.chess).map(([id, r]) => [id, {
      baseId: r.baseId, tier: r.tier, isGolden: !!r.isGolden,
      upgradeNum: r.upgradeNum, upgradeChessId: r.upgradeChessId, bonds: r.bonds || [],
    }])),
    bonds: Object.fromEntries(Object.entries(data.bonds).map(([id, r]) => [id, {
      thresholds: r.thresholds || [], countsHand: !!r.countsHand,
    }])),
  };
}

export function query(observation, candidates, mask, records) {
  const { own: p, public: pub, history } = observation;
  const owned = pieces(p).filter(x => x.kind === 'chess');
  const base = id => records.chess[id]?.baseId || id;
  const core = id => CORE.has(base(id));
  const missing = Math.max(0, p.deployCap - p.deployCount);
  const targetLevel = Math.min(p.shop.maxLevel, 2 + Math.floor(pub.round / 3));
  const refreshes = history.rounds[String(pub.round)]?.refresh || 0;
  const reserve = p.shop.level < targetLevel ? Math.min(6, p.shop.upgradePrice) : 2;
  const scores = candidates.map((a, i) => {
    if (!mask[i]) return { score: null, reason: 'masked' };
    let score = -100, reason = 'low-priority';
    if (a.type === 'ready') { score = 0; reason = 'save-budget'; }
    if (a.type === 'levelUp') {
      score = p.shop.level < targetLevel && (missing === 0 || p.funds - a.price >= 3) ? 24 : -8;
      reason = 'round-level-target';
    }
    if (a.type === 'refresh') {
      score = refreshes < 2 && (p.shop.freeRefreshes > 0 || p.funds - a.price >= reserve + 3) ? 2 : -12;
      reason = 'two-refresh-budget-and-reserve';
    }
    if (a.type === 'buy') {
      const rec = records.chess[a.id];
      if (!rec) return { score: -30, reason: 'items-unscored' };
      const copies = owned.filter(x => x.id === a.id).length;
      const merge = rec.upgradeChessId && copies >= (rec.upgradeNum || 3) - 1;
      const unique = !owned.some(x => base(x.id) === base(a.id));
      let trait = 0;
      for (const id of a.bonds || []) {
        const b = p.bonds.find(b => b.bondId === id);
        if ((pub.disabledBonds || []).includes(id) || b?.off) continue;
        const count = b?.count || 0;
        const next = records.bonds[id]?.thresholds.find(n => n > count);
        // Approximation only: future deployment/trait activation is not guaranteed.
        if (unique && next) trait += next === count + 1 ? 6 : count > 0 ? 1 : 0;
      }
      score = (merge ? 65 : copies ? 12 : 0) + (missing > 0 ? 20 : -9)
        + (core(a.id) ? 9 : 0) + trait + (a.tier || 0) * 2 - a.price * 2;
      if (!merge && missing === 0 && p.funds - a.price < reserve) score -= 12;
      reason = `merge=${!!merge};missing=${missing};core=${core(a.id)};trait=${trait}`;
    }
    if (a.type === 'sell') {
      const piece = owned.find(x => x.uid === a.uid);
      if (piece?.area === 'board' || core(a.id) || piece?.golden || piece?.items?.length) {
        return { score: null, reason: 'protect-board-core-golden-equipped' };
      }
      const copies = owned.filter(x => x.id === a.id).length;
      score = piece?.area === 'temp' ? 8 : !p.hand.includes(null) && copies === 1 ? 3 : -20;
      reason = 'surplus-bench-or-temp';
    }
    return { score, reason };
  });
  let index = -1;
  scores.forEach((x, i) => {
    if (x.score !== null && (index < 0 || x.score > scores[index].score)) index = i;
  });
  if (index < 0 && candidates.length) throw new Error('Heuristic has no unprotected legal action');
  return { teacherIndex: index < 0 ? null : index, teacherVersion: TEACHER_VERSION, teacherScores: scores };
}

// A chooser consumes exactly the same pure query as the labeling wrapper.
export const choose = (observation, candidates, mask, records) => query(observation, candidates, mask, records).teacherIndex;
