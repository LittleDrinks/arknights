// Item decisions take only privateView + static records. No itemTarget/context/pool/RNG.
export const VERSION = 'upstream-drafts+no-sale-arrange+visible-items+free-rewards/v2';
export const LIMITS = Object.freeze({ itemsPerCall: 24, itemsPerRound: 32, itemsPerEpisode: 256,
  rewardsPerRound: 8, arrangePerRound: 128, movesPerRound: 512, events: 2048 });
const states = new WeakMap();
const keyOf = id => String(id).replace(/_[ab]$/, '');
const owned = own => ['board', 'hand', 'temp'].flatMap(area => (own[area] || []).filter(Boolean).map(p => ({ ...p, area })));
const loose = own => owned(own).filter(p => p.kind === 'item');
const chess = own => owned(own).filter(p => p.kind === 'chess');
const free = own => own.hand.filter(p => p === null).length;
const effect = rec => rec?.buffs?.[0]?.key;
const normal = (p, gd) => !gd.chess(p.id)?.isGolden;
const base = (p, gd) => gd.chess(p.id)?.baseId || p.id;
const copies = (p, own, gd) => chess(own).filter(q => normal(q, gd) && base(q, gd) === base(p, gd)).length;
const skip = reason => ({ reason });
const economy = new Set(['equip_destory_gain_random_coin', 'gain_coin_when_round_start', 'use_equip_gain_coin_when_next_round_start']);
const grants = new Set(['use_equip_reward_random_char_chess_in_shop', 'use_equip_reward_char_chess_with_same_bond',
  'use_equip_reward_special_goods_char_chess', 'use_equip_reward_char_chess']);
const upgrades = new Set(['use_equip_upgrade_char', 'equip_round_start_upgrade_char']);
// Pinned data families: new/unknown equipment never falls through to generic equipping.
const regular = new Set(`1_01 1_02 1_05 2_01 2_03 2_04 3_01 3_02 3_03 3_04 3_05 3_06 3_07 3_08 3_09 3_10 3_11 4_02 4_03 4_04 4_05 4_06 4_07 4_08 4_09 4_10 4_11 4_12 5_01 5_02 5_03 5_07 5_09 6_01 6_02 6_03 6_04 6_05 6_06 6_07 6_09 6_10 6_11`.split(' ').map(x => `chess_item_${x}_e`));
const consumables = new Set(`1_03 1_04 2_02 2_05 2_06 2_07 3_12 4_01 5_05 5_06 6_08`.split(' ').map(x => `chess_item_${x}_e`));
function power(p, gd) {
  const c = gd.chess(p.id) || {}, s = c.stats || {};
  return (c.tier || 0) * 8 + (c.isGolden ? 24 : 0) + (s.atk || 0) / Math.max(0.2, s.bat || 1) / 100
    + (s.maxHp || 0) / 1000 + (p.area === 'board' ? 16 : 0);
}
function bonds(p, gd) {
  const out = new Set(gd.chess(p.id)?.bonds || []);
  if ((p.items || []).some(i => gd.item(i.id)?.canGiveBond)) {
    for (const i of p.items || []) { const b = gd.item(i.id)?.giveBondId; if (b) out.add(b); }
  }
  return [...out];
}
function bondValue(p, own, gd) {
  return bonds(p, gd).reduce((n, id) => {
    const b = own.bonds.find(b => b.bondId === id);
    return n + (b?.off ? 0 : 1 + (b?.count || 0) + (b?.active ? 4 : 0));
  }, 0);
}
function rank(list, score) { return [...list].sort((a, b) => score(b) - score(a) || a.uid - b.uid); }
function equipmentScore(p, rec, own, gd) {
  const c = gd.chess(p.id), s = c.stats || {}, params = rec.params || {};
  const healer = c.attackKind === 'heal' || c.dmgType === 'heal';
  const dealer = !healer && c.attackKind !== 'none';
  const defensive = rec.category === 'SURVIVAL' || ((params.def || params.max_hp) && !params.atk);
  let n = power(p, gd);
  n += defensive ? (c.position === 'MELEE' && s.blockCnt > 0 ? 60 + (s.def || 0) / 100 : 0) : (dealer ? 40 : 0);
  if (rec.requiresBondId && bonds(p, gd).includes(rec.requiresBondId)) n += 100;
  if (rec.category === 'BOND_GRANT') n += bondValue(p, own, gd);
  if (params.equip_chess_id && (p.items || []).some(i => keyOf(i.id) === params.equip_chess_id)) n += 30;
  return n;
}

// Pure selector: engine legality remains authoritative. No hidden pool availability check.
export function planItem(own, item, gd, artsUsed = 0) {
  const rec = gd.item(item.id), family = keyOf(item.id);
  if (!rec) return skip('unknown-item-retained');
  if (rec.itemType === 'MAGIC') {
    // Documented onDestroy reward; never activate the extra-enemy bounty.
    if (family === 'chess_item_6_01_m' && rec.buffs?.some(b => b.key === 'trap_disney_special'))
      return { method: 'destroy', args: [item.uid], reason: 'known-destroy-reward' };
    if (rec.buffs?.some(b => b.key === 'trap_create_self_choice')) return skip('bounty-risk-retained');
    if (family !== 'chess_item_6_02_m' || effect(rec) !== 'trap_copy_front_char') return skip('unsupported-art-retained');
    if (artsUsed >= gd.maxArtsPerRound) return skip('art-limit');
    // Copy may also return equipment from three merging copies: reserve conservative headroom.
    const targets = rank(chess(own).filter(p => p.area === 'board' && free(own) >=
      1 + (p.items || []).length + (copies(p, own, gd) >= 2 ? chess(own).filter(q => base(q, gd) === base(p, gd)).reduce((n, q) => n + q.items.length, 0) : 0)), p => power(p, gd) + p.items.length * 15);
    const p = targets[0];
    if (!p) return skip('copy-needs-target-and-hand-room');
    // Current pinned scroll grid starts on its own tile; the engine chooses first chess in grid order.
    if (rec.rangeGrid?.[0]?.[0] !== 0 || rec.rangeGrid?.[0]?.[1] !== 0) return skip('unsupported-art-range');
    return { method: 'useArt', args: [item.uid, p.row, p.col, 'RIGHT'], reason: 'copy-visible-carrier' };
  }
  if (rec.itemType !== 'EQUIP') return skip('unsupported-item-type');
  if (family === 'chess_item_5_04_e' || family === 'chess_item_5_08_e') return skip('destructive-transform-retained');
  if (!regular.has(family) && !consumables.has(family)) return skip('unsupported-equipment-retained');
  let targets = chess(own).filter(p => gd.chess(p.id) && (p.items || []).length < gd.equipPerChess);
  if (!targets.length) return skip('no-free-equipment-slot');
  let score = p => power(p, gd);
  if (consumables.has(family)) {
    const k = effect(rec);
    if (upgrades.has(k)) {
      targets = targets.filter(p => normal(p, gd) && gd.chess(gd.chess(p.id).goldenId) &&
        !(p.items || []).some(i => upgrades.has(effect(gd.item(i.id)))));
      if (k === 'use_equip_upgrade_char' && free(own) < 2) return skip('promotion-needs-hand-room');
    } else if (grants.has(k)) {
      const need = k === 'use_equip_reward_char_chess' ? 3 : Math.max(1, rec.params?.count || 1);
      if (free(own) < need) return skip('grant-needs-hand-room');
      if (k === 'use_equip_reward_random_char_chess_in_shop' && !own.shop.slots.some(s => s && !s.sold && s.kind === 'chess' && gd.chess(s.id) && (!gd.chess(s.id).isDiy || gd.chess(s.id).diyFor))) return skip('no-visible-shop-chess');
      if (k !== 'use_equip_reward_random_char_chess_in_shop') {
        targets = targets.filter(p => bonds(p, gd).some(id => !own.bonds.find(b => b.bondId === id)?.off));
        score = p => bondValue(p, own, gd) * 10 + power(p, gd) +
          (k === 'use_equip_reward_char_chess' && normal(p, gd) && copies(p, own, gd) >= 2 ? 1000 : 0);
      }
    } else if (k === 'use_equip_reward_char_chess_bond_layer') {
      targets = targets.filter(p => bondValue(p, own, gd) > 0);
      score = p => bondValue(p, own, gd) * 100 + power(p, gd);
    } else if (k === 'equip_destory_deployment_cnt_change') {
      if (own.deployCap >= rec.params?.count) return skip('deploy-cap-already-satisfied');
    } else if (!economy.has(k)) return skip('unsupported-consumable-retained');
  } else {
    if (!['passive', 'passive_counter', 'passive_player'].includes(rec.kind)) return skip('unsupported-equipment-kind');
    targets = targets.filter(p => p.area === 'board');
    if (rec.category === 'BOND_GRANT') targets = targets.filter(p => (p.items || []).some(i => {
      const b = gd.item(i.id)?.giveBondId; return b && !bonds(p, gd).includes(b);
    }));
    score = p => equipmentScore(p, rec, own, gd);
  }
  const target = rank(targets, score)[0];
  return target ? { method: 'equip', args: [item.uid, target.uid], reason: consumables.has(family) ? 'beneficial-consumable' : 'role-equipment' } : skip('no-beneficial-legal-target');
}

export function control(env, { forced = false } = {}) {
  const { m, ps, bot } = env;
  if (m.phase !== 'PREP' || ps.ready || !ps.alive) return;
  let s = states.get(env.history);
  if (!s) {
    const audit = env.history.itemController = { version: VERSION, limits: LIMITS, counts: {}, events: [], droppedEvents: 0 };
    s = { audit, failed: new Set(), totalItems: 0, round: null }; states.set(env.history, s);
  }
  if (s.round !== m.round) Object.assign(s, { round: m.round, attempted: new Set(), logged: new Set(), items: 0, rewards: 0, arrangements: 0, moves: 0, rewardFailed: false, arts: 0 });
  const log = (status, reason, extra = {}) => {
    const k = JSON.stringify([status, reason, extra]);
    if (status === 'ignored' && s.logged.has(k)) return;
    s.logged.add(k);
    s.audit.counts[status] = (s.audit.counts[status] || 0) + 1;
    if (s.audit.events.length < LIMITS.events) s.audit.events.push({ round: m.round, status, reason, ...extra }); else s.audit.droppedEvents++;
  };
  const source = env.source;
  const saved = new Map();
  const replace = (name, fn) => { saved.set(name, Object.getOwnPropertyDescriptor(ps, name)); Object.defineProperty(ps, name, { configurable: true, writable: true, value: fn }); };
  env.source = 'fixed-controller'; // Keep the original recorder's error accounting.
  try {
    for (const name of ['buy', 'sell', 'refresh', 'levelUp']) replace(name, () => {
      if (name === 'sell') env.controllerBlockedSales++;
      log('ignored', 'economy-prohibited', { method: name }); return { ok: false, error: 'CONTROLLER_V2_NO_ECONOMY' };
    });
    const move = ps.move.bind(ps);
    replace('move', (...args) => {
      if (s.moves >= LIMITS.movesPerRound) { log('ignored', 'move-budget'); return { ok: false, error: 'CONTROLLER_V2_MOVE_BUDGET' }; }
      s.moves++;
      const result = move(...args);
      log(result?.ok ? 'success' : 'failure', result?.ok ? 'upstream-arrange-move' : String(result?.error || 'move-rejected'), { method: 'move', uid: args[0] });
      return result;
    });
    const arrange = () => {
      if (s.arrangements >= LIMITS.arrangePerRound || s.moves >= LIMITS.movesPerRound) { log('ignored', 'arrange-budget'); return; }
      s.arrangements++; bot.arrange(m, ps);
    };
    m.autoPickPersonalChoice(ps, 'bot');
    arrange();
    let attempts = 0, changed = false;
    // Bounded closure over new items/rewards; each item UID gets at most one attempt per round.
    for (let pass = 0; pass < LIMITS.itemsPerCall; pass++) {
      let progress = false;
      for (const item of loose(ps.privateView())) {
        if (s.failed.has(item.uid) || s.attempted.has(item.uid)) { log('ignored', 'uid-already-attempted', { uid: item.uid, id: item.id }); continue; }
        if (attempts >= LIMITS.itemsPerCall || s.items >= LIMITS.itemsPerRound || s.totalItems >= LIMITS.itemsPerEpisode) { log('ignored', 'item-budget'); break; }
        const own = ps.privateView();
        if (!loose(own).some(p => p.uid === item.uid)) continue;
        const plan = planItem(own, item, ps.gd, s.arts); // successful own Art actions this round
        if (!plan.method) { log('ignored', plan.reason, { uid: item.uid, id: item.id }); continue; }
        s.attempted.add(item.uid); attempts++; s.items++; s.totalItems++;
        const result = ps[plan.method](...plan.args);
        log(result?.ok ? 'success' : 'failure', result?.ok ? plan.reason : String(result?.error || 'engine-rejected'),
          { uid: item.uid, id: item.id, method: plan.method, args: plan.args });
        if (!result?.ok) s.failed.add(item.uid);
        else { if (plan.method === 'useArt') s.arts++; progress = true; changed = true; }
      }
      const own = ps.privateView(), offer = own.shop.rewardOffer;
      if (offer && !s.rewardFailed && s.rewards < LIMITS.rewardsPerRound && free(own) > 0) {
        const idx = offer.slots.findIndex(slot => {
          if (!slot || slot.sold || !Number.isFinite(slot.price) || slot.price > 0) return false;
          const rec = slot.kind === 'item' ? ps.gd.item(slot.id) : slot.kind === 'chess' ? ps.gd.chess(slot.id) : null;
          return !!rec && !(rec.isDiy && !rec.diyFor);
        });
        if (idx >= 0) {
          s.rewards++;
          const result = ps.pickReward(idx);
          log(result?.ok ? 'success' : 'failure', result?.ok ? 'free-visible-reward' : String(result?.error || 'reward-rejected'), { method: 'pickReward', idx });
          s.rewardFailed = !result?.ok; progress ||= !!result?.ok; changed ||= !!result?.ok;
        } else log('ignored', 'no-free-legal-reward');
      } else if (offer) log('ignored', s.rewardFailed ? 'reward-already-failed' : free(own) === 0 ? 'reward-hand-full' : 'reward-budget');
      if (!progress) break;
    }
    if (changed) arrange();
    // Unlike v1, never resolveTemp(): it silently removes unknown items and chess.
    // Fail explicitly before env.step's forced Ready; caller must classify as stopped, not a loss/win.
    const remaining = ps.privateView().temp.filter(Boolean);
    if (forced && remaining.length) {
      log('stop', 'overflow-retained', { pieces: remaining.map(p => ({ uid: p.uid, id: p.id, kind: p.kind })) });
      throw new Error('CONTROLLER_V2_STOP: overflow retained; no disposal or forced Ready');
    }
  } finally {
    for (const [name, descriptor] of saved) { if (descriptor) Object.defineProperty(ps, name, descriptor); else delete ps[name]; }
    env.source = source;
  }
}
