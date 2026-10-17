// Observe real calls and real Battle ticks. Views are never restored into a simulator.
export const copy = value => value == null ? null : JSON.parse(JSON.stringify(value));

export function observation(m, ps) {
  const pub = m.publicView();
  return copy({ public: pub, phase: m.phase, round: m.round, difficulty: m.difficulty,
    mode: m.mode, stageId: m.stageId, disabledBonds: pub.disabledBonds, factions: pub.factions,
    bossId: pub.bossId, draft: pub.draft || null, specialChoice: pub.sp || null,
    teamLp: pub.teamLp ?? null, bossHp: pub.bossHp || null, teammates: pub.players,
    own: ps.privateView() });
}

const methods = {
  buy: slot => ({ t: 'g.buy', slot }), refresh: () => ({ t: 'g.refresh' }),
  freeze: () => ({ t: 'g.freeze' }), levelUp: () => ({ t: 'g.levelUp' }),
  sell: uid => ({ t: 'g.sell', uid }), move: (uid, to, dir) => ({ t: 'g.move', uid, to, dir }),
  equip: (itemUid, targetUid, replaceUid) => ({ t: 'g.equip', itemUid, targetUid, replaceUid }),
  useArt: (itemUid, row, col, dir) => ({ t: 'g.art', itemUid, row, col, dir }),
  destroy: uid => ({ t: 'g.destroy', uid }), pickReward: idx => ({ t: 'g.reward', idx }),
  setReady: ready => ({ t: 'g.ready', ready }), resolveTemp: () => ({ t: 'resolveTemp' }),
};

export class Recorder {
  constructor({ id, guard = () => {}, record = false, maxBytes = 12_000_000, frameEvery = 3 }) {
    Object.assign(this, { id, guard, record, maxBytes, frameEvery, events: [], battles: [], stack: [], traceIndex: null,
      retainedBytes: 0, failure: null });
  }
  check(value) {
    try {
      this.guard();
      if (value) this.retainedBytes += Buffer.byteLength(JSON.stringify(value));
      if (this.retainedBytes > this.maxBytes) throw new Error('Capture exceeds configured output budget');
    } catch (error) { this.failure ||= error; throw error; }
    if (this.failure) throw this.failure;
  }
  capture(m, ps, method, action, fn, assumed = false) {
    // ShopEnv decorates the same methods after construction. Its wrapper must still run,
    // but must not create a second event for the same invocation. Different nested calls stay.
    if (this.stack.some(row => row.method === method && row.playerId === ps.playerId)) return fn();
    this.check();
    const source = sourceOf(this.env, ps, method);
    const row = { type: 'transition', seq: this.events.length, playerId: ps.playerId,
      parentSeq: this.stack.at(-1)?.seq ?? null, source, method, traceIndex: this.traceIndex,
      round: m.round, observation: this.record ? observation(m, ps) : null, action: copy(action), reward: 0, terminated: false };
    this.events.push(row);
    this.stack.push(row);
    try {
      const result = fn();
      row.result = copy(result ?? (assumed ? { ok: true } : null));
      row.accepted = result?.ok === true || (assumed && !result?.error);
      return result;
    } catch (error) {
      row.exception = String(error.stack || error);
      this.failure ||= error;
      throw error;
    } finally {
      this.stack.pop();
      row.next_observation = this.record ? observation(m, ps) : null;
      this.check(row);
    }
  }
  hook(object, name, invoke) {
    // Preserve observation when control() temporarily replaces ps.sell with its no-sale gate,
    // and when ShopEnv installs its history wrapper. No method implementation is skipped.
    const wrappers = new WeakSet();
    const wrap = fn => {
      if (typeof fn !== 'function') throw new Error(`Missing method ${name}`);
      if (wrappers.has(fn)) return fn;
      const wrapped = (...args) => invoke(fn, args);
      wrappers.add(wrapped);
      return wrapped;
    };
    let current = wrap(object[name].bind(object));
    Object.defineProperty(object, name, { configurable: true, enumerable: true,
      get: () => current, set: fn => { current = wrap(fn); } });
  }
  attach(m, Battle) {
    for (const ps of m.players.values()) {
      for (const [method, action] of Object.entries(methods)) {
        this.hook(ps, method, (fn, args) => this.capture(m, ps, method, action(...args),
          () => fn.apply(ps, args), method === 'resolveTemp'));
      }
    }
    for (const [method, action] of [
      ['_applyBand', bandId => ({ t: 'g.band', bandId })],
      ['_applyCard', idx => ({ t: 'g.choice', idx })],
      ['pickPersonalChoice', (idx, choiceId) => ({ t: 'g.choice', idx, choiceId })],
    ]) {
      this.hook(m, method, (fn, [ps, ...args]) => this.capture(m, ps, method, action(...args),
        () => fn.call(m, ps, ...args), method.startsWith('_apply')));
    }
    const recorder = this;
    // newBattle's explicit preview flag is authoritative, including zero-spawn real battles.
    // Rehearsals have their own field prefix and are excluded as a second guard.
    let previewDepth = 0;
    const newBattle = m.newBattle.bind(m);
    m.newBattle = (opts, options) => {
      if (options?.preview) previewDepth++;
      try { return newBattle(opts, options); }
      finally { if (options?.preview) previewDepth--; }
    };
    m.BattleClass = class RecordedBattle extends Battle {
      constructor(opts) {
        const record = recorder.record && !previewDepth && !opts.fieldId?.startsWith('r:');
        super(record ? { ...opts, recordEvents: true } : opts);
        this.captureBattle = record;
        if (!record) { this.pending = []; return; }
        this.pending ||= []; // _ev can run during super(): retain those events too.
        const name = `${recorder.id}-r${m.round}-${recorder.battles.length}`;
        this.recording = { version: 1, name, title: `合作训练轨迹（跨 PPO 行为版本） · ${m.difficulty} · R${m.round}`,
          kind: opts.kind, stageId: m.stageId, modeId: m.modeId, round: m.round,
          tick: this.dt, participants: opts.players.map(p => p.playerId), frameEvery: recorder.frameEvery, field: null, frames: [] };
        recorder.battles.push(this);
      }
      _ev(tuple) {
        super._ev(tuple);
        if (recorder.record && this.captureBattle !== false && this.recordEvents && !previewDepth && !this.fieldId?.startsWith('r:')) {
          (this.pending ||= []).push(copy(tuple));
          recorder.check(tuple);
        }
      }
      frame() {
        if (!this.recording) return;
        const rec = this.recording;
        rec.field ||= copy(this.fieldMeta());
        const frame = { snap: copy(this.snapshot()), ev: this.pending.splice(0) };
        const last = rec.frames.at(-1);
        if (last?.snap.t === frame.snap.t) { last.ev.push(...frame.ev); last.snap = frame.snap; }
        else rec.frames.push(frame);
        recorder.check(frame.snap);
      }
      start() { const result = super.start(); this.frame(); return result; }
      step() {
        recorder.check();
        const result = super.step();
        if (this.tickCount % recorder.frameEvery === 0 || this.finished) this.frame();
        return result;
      }
      forceEnd(reason) { const result = super.forceEnd(reason); this.frame(); return result; }
    };
  }
  finish(env) {
    this.check();
    if (!this.record) return [];
    for (const ps of env.m.players.values()) {
      const row = this.events.findLast(row => row.playerId === ps.playerId && row.parentSeq === null && row.source !== 'engine');
      if (!row) throw new Error('Missing player transitions');
      row.terminated = true;
      row.reward = env.summary.victory ? 1 : -1;
      row.terminal_observation = observation(env.m, ps);
      // Keep the actual method-after view separately; match the upstream viewer terminal convention.
    }
    return this.battles.map(b => {
      if (!b.finished || b.errorCount) throw new Error('Unfinished or errored real battle');
      b.frame();
      return { ...b.recording, duration: b.time, result: copy(b.result()) };
    });
  }
}

// Attribution must use the called seat, never env.source alone: the scheduler runs
// partners while the learner source is still "policy". Drafts and cleanup stay distinct.
export function sourceOf(env, ps, method) {
  if (method.startsWith('_apply') || method === 'pickPersonalChoice') return 'fixed-draft-controller';
  if (method === 'resolveTemp') return ps.playerId === 'learner' && env.source === 'fixed-controller' && !ps.ready
    ? 'budget-controller' : 'engine';
  return ps.playerId === 'learner' ? env.source : 'partner-controller';
}

export const PLAYER_MAP = Object.freeze({ learner: 'ai_0', 'partner-1': 'ai_1', 'partner-2': 'ai_2', 'partner-3': 'ai_3' });
// Export copies only. IDs may also appear as object keys or in n:<playerId> fields.
export function viewerIds(value) {
  const rename = s => {
    for (const [id, mapped] of Object.entries(PLAYER_MAP)) {
      if (s === id) return mapped;
      if (s.endsWith(`:${id}`)) return `${s.slice(0, -id.length)}${mapped}`;
    }
    return s;
  };
  if (typeof value === 'string') return rename(value);
  if (Array.isArray(value)) return value.map(viewerIds);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).map(([k, v]) => [rename(k), viewerIds(v)]));
  return value;
}
