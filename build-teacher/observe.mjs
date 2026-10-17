// Optional read-only observer for upstream botbench --jobs 1. No decisions here.
// Every wrapper invokes its original once and returns its unchanged result.
import { appendFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { CONFIG, BUILDS, reports } from './policy.mjs';
const root = resolve(process.env.ARK_BUILD_GAME_ROOT ?? fileURLToPath(new URL('../../Stronghold-Protocol/', import.meta.url)));
const { Match } = await import(pathToFileURL(resolve(root, 'server/match/Match.js')));
const path = process.env.ARK_BUILD_TRACE;
if (!path) throw new Error('build-teacher observer requires ARK_BUILD_TRACE');
let bytes = 0;
function emit(row) {
  const line = JSON.stringify(row) + '\n';
  bytes += Buffer.byteLength(line);
  if (bytes > 8 * 1024 * 1024) throw new Error('build-teacher: trace limit');
  appendFileSync(path, line);
}
export function snapshot(ps) {
  const units = [...ps.board].filter(([, p]) => p.kind === 'chess').map(([key, p]) => ({
    uid: p.uid, id: p.id, name: ps.gd.chess(p.id)?.name, key, dir: p.dir ?? 'RIGHT',
    items: p.items.map(i => ({ id: i.id, name: ps.gd.item(i.id)?.name })),
  }));
  const core = {};
  for (const [id, b] of Object.entries(BUILDS)) core[id] = {
    owned: [...new Set(ps.allChess().map(p => ps.gd.chess(p.id)?.name).filter(n => b.names.includes(n)))],
    deployed: [...new Set(units.map(p => p.name).filter(n => b.names.includes(n)))],
    bond: structuredClone(ps.bonds[b.bond] ?? null),
  };
  const a = units.find(p => p.name === '圣聆初雪'), b = units.find(p => p.name === '灵知');
  let adjacency = null;
  if (a && b) {
    const [r,c] = a.key.split(',').map(Number), [r1,c1] = b.key.split(',').map(Number);
    const dr = Math.abs(r-r1), dc = Math.abs(c-c1);
    adjacency = { orthogonal: dr+dc===1, surrounding: Math.max(dr,dc)===1,
      effective: !!ps.bonds.skillfulShip?.active && ((ps.layers.skillfulShip ?? 0) >= 40 ? Math.max(dr,dc)===1 : dr+dc===1) };
  }
  return { pid: ps.playerId, funds: ps.funds, lp: ps.lp, alive: ps.alive, level: ps.shop.level,
    units: ps.deployCount, cap: ps.deployCap, refreshes: ps.round.refreshes,
    layers: { ...ps.layers }, activatedLayers: ps.activatedLayers(), bonds: structuredClone(ps.bonds),
    core, board: units, hand: ps.hand.filter(Boolean).map(p => ({ uid:p.uid, id:p.id, kind:p.kind })),
    temp: ps.temp.filter(Boolean).map(p => ({ uid:p.uid, id:p.id, kind:p.kind })), adjacency,
    policy: structuredClone(reports(ps)) };
}
const start = Match.prototype.start;
Match.prototype.start = function (...args) {
  const m = this;
  const common = () => ({ seed: m.seed, round: m.round, phase: m.phase });
  const pool = () => m.bossPool ? { hp:m.bossPool.hp, maxHp:m.bossPool.maxHp, teamLp:m.teamLp } : null;
  emit({ type:'start', ...common(), config:CONFIG, stage:m.stageId, disabledBonds:m.disabledBonds,
    loadouts: m.order.map(p => ({ pid:p.playerId, loadout:p.loadout, diy: p.diyStock?.entries?.size ?? 0 })) });
  const dispatch = m.dispatch;
  m.dispatch = function (ps, hook, ev = {}, ...rest) {
    const result = dispatch.call(this, ps, hook, ev, ...rest);
    if (hook === 'onPrepEnd') emit({ type:'prep', ...common(), state:snapshot(ps), boss:pool() });
    if (hook === 'onBattleResult') emit({ type:'result', ...common(), state:snapshot(ps), boss:pool(),
      battle: { boss:!!ev.boss, killed:ev.result?.killed, leaks:ev.result?.leaked?.length,
        damage:ev.result?.damageDealt, perfect:ev.perfect, lpLoss:ev.lpLoss, layerGains:ev.result?.layerGains } });
    return result;
  };
  for (const ps of m.order) {
    for (const key of ['buy', 'sell', 'equip', 'pickReward', 'levelUp', 'refresh', 'setReady']) {
      const fn = ps[key];
      ps[key] = function (...xs) {
        const before = ps.funds;
        const visible = key === 'buy' ? structuredClone(ps.shop.slots[xs[0]])
          : key === 'pickReward' ? structuredClone(ps.offers[0]?.slots?.[xs[0]]) : null;
        const piece = ['sell','equip'].includes(key) ? ps.find(xs[0])?.piece : null;
        const source = piece ? { uid:piece.uid, id:piece.id } : null;
        const res = fn.apply(this, xs);
        emit({ type:'action', ...common(), pid:ps.playerId, action:key, args:xs, visible, source,
          before, after:ps.funds, result:res });
        return res;
      };
    }
    const resolveTemp = ps.resolveTemp;
    ps.resolveTemp = function (...xs) {
      const before = ps.temp.filter(p => p?.kind === 'chess').map(p => ({uid:p.uid,id:p.id}));
      const res = resolveTemp.apply(this, xs);
      const removed = before.filter(p => !ps.find(p.uid));
      if (removed.length) emit({ type:'native-temp-discard', ...common(), pid:ps.playerId, removed });
      return res;
    };
  }
  const newBattle = m.newBattle;
  m.newBattle = function (...xs) {
    const b = newBattle.apply(this, xs);
    b.on('battleEnd', () => emit({ type:'battle-end', ...common(), field:b.fieldId, time:b.time,
      reason:b.reason, boss:pool(), survivors:b.allyUnits.filter(u => u.kind === 'op' && u.hp > 0)
        .map(u => ({ id:u.defId, hp:u.hp, owner:u.ownerId })) }), {owner:'build-teacher-observer', priority:-9999});
    return b;
  };
  const finish = m.finish;
  m.finish = function (...xs) {
    const res = finish.apply(this, xs);
    emit({ type:'finish', ...common(), outcome:m.outcome, boss:pool(), errors:m.errorCount,
      maxRssKiB:process.resourceUsage().maxRSS, players:m.order.map(snapshot) });
    return res;
  };
  return start.apply(this, args);
};
