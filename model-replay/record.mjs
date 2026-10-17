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
  constructor({ id, guard = () => {} }) {
    Object.assign(this, { id, guard, events: [], battles: [], stack: [], traceIndex: null,
      retainedBytes: 0, failure: null });
  }
  check(value) {
    try {
      this.guard();
      if (value) this.retainedBytes += Buffer.byteLength(JSON.stringify(value));
      if (this.retainedBytes > 20_000_000) throw new Error('Capture exceeds 20 MB');
    } catch (error) { this.failure ||= error; throw error; }
    if (this.failure) throw this.failure;
  }
  capture(m, ps, method, action, fn, assumed = false) {
    // ShopEnv decorates the same methods after construction. Its wrapper must still run,
    // but must not create a second event for the same invocation. Different nested calls stay.
    if (this.stack.some(row => row.method === method && row.playerId === ps.playerId)) return fn();
    this.check();
    const source = method.startsWith('_apply') || method === 'pickPersonalChoice'
      ? 'fixed-draft-controller'
      : method === 'resolveTemp' ? (this.env.source === 'fixed-controller' && !ps.ready
        ? 'budget-controller' : 'engine')
        : this.env.source;
    const row = { type: 'transition', seq: this.events.length, playerId: ps.playerId,
      parentSeq: this.stack.at(-1)?.seq ?? null, source, method, traceIndex: this.traceIndex,
      observation: observation(m, ps), action: copy(action), reward: 0, terminated: false };
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
      row.next_observation = observation(m, ps);
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
        const record = !previewDepth && !opts.fieldId?.startsWith('r:');
        super(record ? { ...opts, recordEvents: true } : opts);
        if (!record) { this.pending = []; return; }
        this.pending ||= []; // _ev can run during super(): retain those events too.
        const name = `${recorder.id}-r${m.round}-${recorder.battles.length}`;
        this.recording = { version: 1, name, title: `学习模型实际对局 · ${m.difficulty} · R${m.round}`,
          kind: opts.kind, stageId: m.stageId, modeId: m.modeId, round: m.round,
          tick: this.dt, field: null, frames: [] };
        recorder.battles.push(this);
      }
      _ev(tuple) {
        super._ev(tuple);
        if (this.recordEvents && !previewDepth && !this.fieldId?.startsWith('r:')) {
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
        if (this.tickCount % 3 === 0 || this.finished) this.frame();
        return result;
      }
      forceEnd(reason) { const result = super.forceEnd(reason); this.frame(); return result; }
    };
  }
  finish(env) {
    this.check();
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

// agentReplay.js filters by ai_<seat>. Apply only to exported copies; never alter engine IDs,
// seeds, candidates or UIDs. Object keys include BattleResult.perPlayer and snapshot.dps.
export function viewerIds(value) {
  const rename = s => s === 'learner' ? 'ai_0' : s.endsWith(':learner') ? `${s.slice(0, -7)}ai_0` : s;
  if (typeof value === 'string') return rename(value);
  if (Array.isArray(value)) return value.map(viewerIds);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).map(([k, v]) => [rename(k), viewerIds(v)]));
  return value;
}
