// Synthetic inputs only: not a claim of legal/reachable game states.
export const data = { chess: { b: {}, a: {} }, items: { item: {} }, bonds: { bond: {} }, bands: { band: {} } };
const piece = (id, kind = 'chess', golden = true) => ({ id, kind, golden });
export function observation() {
  return {
    own: {
      board: [piece('a'), piece('a'), piece('a', 'token'), piece('item', 'item'), piece('b', 'chess', false)],
      hand: [piece('b'), null, piece('a', 'chess', 'true')],
      temp: [piece('a'), piece('b'), piece('unknown')], bonds: [], bandId: 'band',
      lp: 100, funds: 10, deployCount: 3, deployCap: 6,
      shop: { level: 1, upgradePrice: 4, refreshPrice: 1, freeRefreshes: 0, frozen: false, slots: [] },
    },
    public: { round: 1, difficulty: 'NORMAL', disabledBonds: [] },
    history: { rounds: {}, pieces: {}, byId: {}, totals: {}, prepSteps: 0, prepBudget: 8 },
  };
}
export const candidates = [{ type: 'sell', uid: 'a1', id: 'a', golden: true, price: 1 }, { type: 'ready' }];
