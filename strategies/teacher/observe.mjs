// Optional read-only instrumentation for ORIGINAL tools/botbench.mjs (--jobs 1).
// Wrappers call each original method exactly once, with unchanged arguments/results.
import { writeFileSync } from 'node:fs';
import { Match } from '../../Stronghold-Protocol/server/match/Match.js';
import { IDS, options, reports } from './policy.mjs';
const matches = [];
const target = ['siracusaShip', 'arcaneShip', 'yanShip'];
const snapshot = ps => ({ funds: ps.funds, level: ps.shop.level,
  refreshes: ps.round.refreshes, gainedChess: ps.round.gainedChess,
  bonds: Object.fromEntries(target.map(b => [b, { ...ps.bonds[b], layers: ps.layers[b] ?? 0 }])),
  board: [...ps.board.values()].filter(p => p.kind === 'chess').map(p => ({ uid: p.uid, id: p.id, items: p.items.map(i => i.id) })),
  hand: ps.hand.filter(p => p?.kind === 'chess').map(p => ({ uid: p.uid, id: p.id })),
});
const start = Match.prototype.start;
Match.prototype.start = function (...args) {
  const m = this;
  const rec = { seed: m.seed, config: options(), buys: [], layers: [], preps: [] };
  matches.push(rec);
  const dispatch = m.dispatch;
  m.dispatch = function (ps, hook, ev = {}, ...rest) {
    const before = hook === 'onPrepEnd' ? snapshot(ps) : null;
    if (hook === 'onBuy' && Object.values(IDS).includes(ps.gd.baseIdOf(ev.piece?.id ?? ''))) {
      rec.buys.push({ round: m.round, pid: ps.playerId, id: ev.piece.id, uid: ev.piece.uid, price: ev.price, funds: ps.funds });
    }
    if (hook === 'onLayers' && target.includes(ev.bondId)) {
      rec.layers.push({ round: m.round, phase: m.phase, pid: ps.playerId, ...structuredClone(ev) });
    }
    const result = dispatch.call(this, ps, hook, ev, ...rest);
    if (before) rec.preps.push({ round: m.round, pid: ps.playerId, before, after: snapshot(ps),
      teacher: structuredClone(reports(ps)) });
    return result;
  };
  return start.apply(this, args);
};
process.on('exit', () => {
  if (!process.env.ARK_TEACHER_TRACE) return;
  const text = JSON.stringify(matches);
  if (Buffer.byteLength(text) > 8 * 1024 * 1024) throw new Error('teacher trace exceeds 8 MiB');
  writeFileSync(process.env.ARK_TEACHER_TRACE, text);
});
