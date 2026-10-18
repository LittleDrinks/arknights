import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Called after ShopEnv.create has checked the rule pin and clean source tree.
export async function loadMovePreflight(game = process.env.STRONGHOLD_GAME ||
  fileURLToPath(new URL('../../Stronghold-Protocol', import.meta.url))) {
  const rules = await import(pathToFileURL(path.resolve(game, 'server/match/board.js')));
  return (ps, uid, to, dir) => badTileReason(ps, uid, to, dir, rules);
}

// Only predict BAD_TILE, in the order of player/placement.js at the pinned ref.
// No move/recompute/detach simulation. _legal runs on a read-through receiver so
// deployMap's lazy cache/stale writes remain local; the live board is never edited.
function badTileReason(ps, uid, to, dir, { parseDir, inField, tileKey, parseKey, pieceDir }) {
  if (ps._gate()) return null;
  const loc = ps.find(uid);
  if (!loc || loc.area === 'equipped' || !to || typeof to !== 'object') return null;
  const piece = loc.piece, probe = Object.create(ps);
  const legal = (p, r, c, owner = null) => ps._legal.call(probe, p, r, c, owner);
  if (to.area === 'board') {
    if (piece.kind === 'item' || !parseDir(dir !== undefined ? dir : to.dir)) return null;
    const { row: r, col: c } = to;
    if (!inField(r, c)) return 'destination-out-of-field';
    const occ = ps.board.get(tileKey(r, c));
    const ownerAfter = piece.kind === 'token' && loc.area === 'board' && occ && occ !== piece &&
      occ.uid === piece.ownerUid ? { key: loc.key, piece: occ, dir: pieceDir(occ) } : null;
    if (!legal(piece, r, c, ownerAfter)) return ownerAfter ? 'summon-owner-swap-destination' : 'destination-illegal';
    if (occ === piece) return null;
    if (loc.area === 'board' && occ) {
      // A chess moving onto its OWN summon lifts it; it does not need a legal return tile.
      const lifted = piece.kind !== 'token' && occ.kind === 'token' && occ.ownerUid === piece.uid;
      if (!lifted && !legal(occ, ...parseKey(loc.key))) return 'swap-return-illegal';
    } else if (loc.area !== 'board' && piece.kind === 'token' && occ) {
      return 'summon-destination-occupied';
    }
  } else if (to.area === 'hand' && Number.isInteger(to.idx) && to.idx >= 0 && to.idx < ps.hand.length) {
    const occ = ps.hand[to.idx];
    if (piece.kind !== 'token' && loc.area === 'board' && occ !== piece && occ?.kind === 'chess' &&
        !legal(occ, ...parseKey(loc.key))) return 'hand-swap-return-illegal';
  }
  return null;
}

export const filteredMove = reason => ({ ok: false, error: 'COOP_BAD_TILE_FILTER', reason });
