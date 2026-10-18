// Adapt only the no-sale controller's planner proposals. Original bot seats keep native prep.
// A full lineup + full hand can prevent arrange from withdrawing its old choice; its next
// proposal would then exceed the visible cap. Filter BEFORE submitting a real engine action.
import { filteredMove } from './preflight.mjs';

export function capacityFilteredBot(bot, onBlocked, arranging = new WeakSet(), preflight = () => null) {
  return { ...bot, arrange(m, ps, opts) {
    const move = ps.move;
    ps.move = (uid, to, dir) => {
      const reason = preflight(ps, uid, to, dir);
      if (reason) {
        onBlocked({ playerId: ps.playerId, round: m.round, args: [uid, to, dir], reason });
        return filteredMove(reason);
      }
      if (to?.area === 'board') {
        const own = ps.privateView();
        const off = [...own.hand, ...own.temp].find(p => p?.uid === uid);
        const occupied = own.board.find(p => p.row === to.row && p.col === to.col);
        if (off?.kind === 'chess' && occupied?.kind !== 'chess' && own.deployCount >= own.deployCap) {
          onBlocked({ playerId: ps.playerId, round: m.round, args: [uid, to, dir], reason: 'capacity' });
          return { ok: false, error: 'COOP_CONTROLLER_CAPACITY_FILTER' };
        }
      }
      return move.call(ps, uid, to, dir);
    };
    arranging.add(ps);
    try { return bot.arrange(m, ps, opts); } finally { ps.move = move; arranging.delete(ps); }
  } };
}
