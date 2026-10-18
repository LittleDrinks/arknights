// Synthetic only. Never imports or constructs a game Match/Battle.
import { Recorder, PLAYER_MAP } from '../coop-replay/record.mjs';
import { PROFILE, CONTROLLER } from './profile.mjs';

export function fixture(provenance, terminal) {
  const players = new Map(Object.keys(PLAYER_MAP).map((playerId, seat) => {
    const ps = { playerId, seat, alive: true, ready: false,
      privateView() { return { playerId, funds: 4, lp: this.alive ? 2 : 0, alive: this.alive, ready: this.ready }; } };
    for (const method of ['buy', 'sell', 'refresh', 'freeze', 'levelUp', 'move', 'equip',
      'useArt', 'destroy', 'pickReward', 'setReady', 'resolveTemp']) ps[method] = () => ({ ok: true });
    ps.setReady = ready => { ps.ready = ready; ps.resolveTemp(); return { ok: true }; };
    return [playerId, ps];
  }));
  const m = { players, phase: 'BAND_DRAFT', round: 0, difficulty: 'ABYSS', mode: 'coop', stageId: 'synthetic',
    publicView() { return { players: [...players.values()].map(p => ({ playerId: p.playerId, seat: p.seat, alive: p.alive })) }; },
    _applyBand() {}, _applyCard() {}, pickPersonalChoice: () => ({ ok: true }),
    newBattle(opts) { return new this.BattleClass(opts); } };
  const summary = structuredClone(terminal.summary);
  const env = { m, source: 'policy', summary };
  const recorder = new Recorder({ id: 'synthetic', record: true, profile: PROFILE, maxBytes: 2_000_000 });
  recorder.env = env; recorder.attach(m, FakeBattle);
  for (const ps of players.values()) m._applyBand(ps, 'synthetic-band');
  const battle = (fieldId, participants, kind) => {
    const b = m.newBattle({ fieldId, players: participants, kind });
    b.start(); for (let i = 0; i < 4; i++) b.step();
  };
  for (let round = 1; round <= 14; round++) {
    m.phase = 'PREP'; m.round = round;
    if (round === 13) players.get('learner').alive = false;
    const alive = [...players.values()].filter(p => p.alive);
    for (const ps of alive) {
      ps.ready = false; ps.buy(1); ps.setReady(true);
      battle(`n:${ps.playerId}`, [ps], round === 14 ? 'boss' : 'normal');
    }
    if (round <= 10) battle('u', alive, 'unite');
  }
  m.phase = 'RESULT';
  const recordings = recorder.finish(env);
  const { ok, ...state } = structuredClone(terminal);
  state.controller = CONTROLLER;
  return { id: 'synthetic', state, recorder, recordings, provenance, profile: PROFILE, maxBytes: 4_000_000 };
}

class FakeBattle {
  constructor(opts) {
    this.fieldId = opts.fieldId; this.recordEvents = opts.recordEvents !== false;
    this.dt = 1 / 30; this.time = 0; this.tickCount = 0;
    this._ev(['spawn', { id: 7, playerId: opts.players[0].playerId }]);
  }
  _ev() {}
  start() { this._ev(['start']); }
  step() { this.tickCount++; this.time = this.tickCount / 30; this._ev(['tick', this.tickCount]); this.finished = this.tickCount === 4; }
  fieldMeta() { return { fieldId: this.fieldId, rect: {}, units: [] }; }
  snapshot() { return { fieldId: this.fieldId, t: this.time, units: [[7]], dps: { 'partner-1': 5 } }; }
  result() { return { errors: 0, perPlayer: { 'partner-1': { kills: 1 } } }; }
}
