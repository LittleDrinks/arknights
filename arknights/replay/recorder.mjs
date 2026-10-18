// Observe real method invocations and Battle ticks. Never restore views into a simulator.
import assert from 'node:assert/strict';
import { safeId, DEFAULT_MAX_BYTES, DEFAULT_PROFILE, writeBundle } from './export.mjs';
export { copy, PLAYER_MAP, viewerIds } from './export.mjs';

export function observation(m, ps) {
  const pub = m.publicView();
  return { public: pub, phase: m.phase, round: m.round, difficulty: m.difficulty, mode: m.mode,
    stageId: m.stageId, disabledBonds: pub.disabledBonds, factions: pub.factions, bossId: pub.bossId,
    draft: pub.draft || null, specialChoice: pub.sp || null, teamLp: pub.teamLp ?? null,
    bossHp: pub.bossHp ?? null, teammates: pub.players, own: ps.privateView() };
}

const methods = {
  buy: slot => ({ t: 'g.buy', slot }), refresh: () => ({ t: 'g.refresh' }),
  freeze: () => ({ t: 'g.freeze' }), levelUp: () => ({ t: 'g.levelUp' }),
  sell: uid => ({ t: 'g.sell', uid }), move: (uid, to, dir) => ({ t: 'g.move', uid, to, dir }),
  equip: (itemUid, targetUid, replaceUid) => ({ t: 'g.equip', itemUid, targetUid, replaceUid }),
  useArt: (itemUid, row, col, dir) => ({ t: 'g.art', itemUid, row, col, dir }),
  destroy: uid => ({ t: 'g.destroy', uid }), pickReward: idx => ({ t: 'g.reward', idx }),
  setReady: ready => ({ t: 'g.ready', ready }), resolveTemp: () => ({ t: 'resolveTemp' }),
  setLoadout: (loadout, ops) => ({ t: 'setLoadout', loadout, ops }),
  setNotOwned: ids => ({ t: 'setNotOwned', ids }), setDiy: picks => ({ t: 'setDiy', picks }),
};
const matchMethods = {
  _applyBand: bandId => ({ t: 'g.band', bandId }), pickBand: bandId => ({ t: 'g.band', bandId }),
  skipBand: () => ({ t: 'g.bandSkip' }), bandFocus: bandId => ({ t: 'g.bandFocus', bandId }),
  _applyCard: idx => ({ t: 'g.choice', idx }), pickCard: idx => ({ t: 'g.choice', idx }),
  pickPersonalChoice: (idx, choiceId) => ({ t: 'g.choice', idx, choiceId }),
  voteSetupReroll: (voteId, agree) => ({ t: 'g.rerollVote', voteId, agree }),
  setAutoplay: on => ({ t: 'g.autoplay', on }), setPause: on => ({ t: 'g.pause', on }),
  emote: id => ({ t: 'g.emote', id }), watch: (fieldId, playerId) => ({ t: 'g.watch', fieldId, playerId }),
};
const isLearner = ps => ['learner', 'ai0', 'ai_0'].includes(ps.playerId) || ps.seat === 0;
const isDraft = method => ['_applyBand', '_applyCard', 'pickBand', 'skipBand', 'bandFocus', 'pickCard', 'pickPersonalChoice'].includes(method);
export function sourceOf(context, ps, method) {
  if (method === 'resolveTemp') return 'engine';
  if (!isLearner(ps)) return 'partner-controller';
  if (isDraft(method)) return context?.source === 'policy' ? 'policy' : 'fixed-draft-controller';
  return context?.source ?? 'policy';
}

export class Recorder {
  constructor({ id, recordDir, frameEvery = 3, maxBytes = DEFAULT_MAX_BYTES, profile = {}, guard = () => {} }) {
    safeId(id); assert(Number.isSafeInteger(frameEvery) && frameEvery > 0, 'Invalid frameEvery');
    assert(Number.isSafeInteger(maxBytes) && maxBytes > 0, 'Invalid maxBytes');
    Object.assign(this, { id, recordDir, frameEvery, maxBytes, guard,
      profile: { ...DEFAULT_PROFILE, ...profile }, events: [], commands: [], battles: [], stack: [],
      retainedBytes: 0, failure: null, commandEventStart: 0, traceIndex: null });
  }
  check(value) {
    if (this.failure) throw this.failure;
    try {
      this.guard();
      const bytes = typeof value === 'number' ? value : value === undefined ? 0 : Buffer.byteLength(JSON.stringify(value));
      assert(this.retainedBytes + bytes <= this.maxBytes, 'Capture budget exceeded');
      this.retainedBytes += bytes;
    } catch (error) { this.failure ||= error; throw error; }
  }
  // Account serialized payloads before retaining copies. Accounting is conservative:
  // released/replaced frames are not refunded, and every container has headroom.
  keep(value) {
    const json = JSON.stringify(value ?? null);
    this.check(Buffer.byteLength(json) + 64);
    return JSON.parse(json);
  }
  capture(m, ps, method, action, fn) {
    if (this.stack.some(row => row.method === method && row.playerId === ps.playerId)) return fn();
    this.check();
    const row = this.keep({ type: 'transition', seq: this.events.length, playerId: ps.playerId,
      parentSeq: this.stack.at(-1)?.seq ?? null, source: sourceOf(this.env ?? this, ps, method), method,
      traceIndex: this.traceIndex, round: m.round, observation: observation(m, ps),
      action, reward: null, terminated: false, accepted: false });
    this.events.push(row); this.stack.push(row);
    try {
      const result = fn();
      row.result = this.keep(result);
      // Void engine helpers have no acknowledgement; retain null instead of inventing success.
      row.accepted = result?.ok === true;
      return result;
    } catch (error) {
      this.failure ||= error;
      throw error;
    } finally {
      this.stack.pop();
      if (!this.failure) row.next_observation = this.keep(observation(m, ps));
    }
  }
  hook(object, name, invoke) {
    if (typeof object[name] !== 'function') return;
    const wrappers = new WeakSet();
    const wrap = fn => {
      assert(typeof fn === 'function', `Missing method ${name}`);
      if (wrappers.has(fn)) return fn;
      const wrapped = (...args) => invoke(fn, args); wrappers.add(wrapped); return wrapped;
    };
    let current = wrap(object[name]);
    Object.defineProperty(object, name, { configurable: true, enumerable: true,
      get: () => current, set: fn => { current = wrap(fn); } });
  }
  attach(m, Battle) {
    assert(!this.match, 'Recorder already attached'); assert(typeof Battle === 'function', 'Battle class required');
    this.match = m; this.playerIds = [...m.players.keys()];
    assert(this.playerIds.length > 0, 'No players');
    for (const ps of m.players.values()) for (const [method, action] of Object.entries(methods))
      this.hook(ps, method, (fn, args) => this.capture(m, ps, method, action(...args), () => fn.apply(ps, args)));
    for (const [method, action] of Object.entries(matchMethods))
      this.hook(m, method, (fn, [ps, ...args]) => this.capture(m, ps, method, action(...args), () => fn.call(m, ps, ...args)));
    // Catch remaining protocol operations (infoReady, leave, etc.) that do not
    // delegate to a captured method; covered operations are recorded at their real seam.
    this.hook(m, '_handle', (fn, [ps, msg]) => {
      const known = new Set(['g.buy', 'g.refresh', 'g.freeze', 'g.levelUp', 'g.sell', 'g.move', 'g.equip',
        'g.art', 'g.destroy', 'g.reward', 'g.ready', ...Object.values(matchMethods).map(f => f().t)]);
      return known.has(msg.t) ? fn.call(m, ps, msg)
        : this.capture(m, ps, '_handle', msg, () => fn.call(m, ps, msg));
    });
    const recorder = this;
    let previewDepth = 0;
    const newBattle = m.newBattle;
    m.newBattle = function(opts, options) {
      if (options?.preview) previewDepth++;
      try { return newBattle.call(this, opts, options); }
      finally { if (options?.preview) previewDepth--; }
    };
    m.BattleClass = class RecordedBattle extends Battle {
      constructor(opts) {
        const capture = !previewDepth && !opts.fieldId?.startsWith('r:');
        super(capture ? { ...opts, recordEvents: true } : opts);
        this.captureBattle = capture;
        this.pending ||= [];
        if (!capture) return;
        this.recording = recorder.keep({ version: 1, name: `${recorder.id}-r${m.round}-${recorder.battles.length}`,
          title: `${recorder.profile.battleLabel} · ${m.difficulty} · R${m.round}`, kind: opts.kind,
          stageId: m.stageId, modeId: m.modeId, round: m.round, tick: this.dt,
          participants: opts.players.map(p => p.playerId), frameEvery: recorder.frameEvery, field: null, frames: [] });
        // Retain only replay data, never the full simulation object.
        recorder.battles.push(this.recording);
      }
      _ev(tuple) {
        super._ev(tuple);
        if (this.captureBattle !== false && this.recordEvents && !previewDepth && !this.fieldId?.startsWith('r:'))
          (this.pending ||= []).push(recorder.keep(tuple));
      }
      frame() {
        const rec = this.recording;
        if (!rec) return;
        recorder.check();
        rec.field ||= recorder.keep(this.fieldMeta());
        const last = rec.frames.at(-1);
        if (last?.snap.t !== this.time) {
          recorder.check(64);
          rec.frames.push({ snap: recorder.keep(this.snapshot()), ev: this.pending.splice(0) });
        } else if (this.pending.length) {
          last.snap = recorder.keep(this.snapshot());
          for (const ev of this.pending) last.ev.push(ev);
          this.pending.length = 0;
        }
        if (this.finished && !Object.hasOwn(rec, 'result')) {
          rec.duration = this.time; rec.result = recorder.keep(this.result());
        }
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
    return this;
  }
  // before/after are caller-sanitized ONLINE dictionaries. Never call a view builder
  // here: the explicit log must exactly preserve what the policy could actually see.
  recordCommand(command, before, after, result) {
    assert(!this.finished, 'Recorder already finished'); this.check();
    const row = this.keep({ seq: this.commands.length, command, before, after, result,
      firstEvent: this.commandEventStart, lastEvent: this.events.length - 1 });
    this.commands.push(row);
    // A direct external learner draft can be identified after dispatch as well as
    // through recorder.source='policy'. Never relabel automatic or partner drafts.
    const action = command?.action ?? command?.intent ?? command;
    const explicit = typeof action?.t === 'string' ? action.t : command?.cmd?.startsWith('g.') ? command.cmd : null;
    if (explicit) for (let i = this.commandEventStart; i < this.events.length; i++) {
      const event = this.events[i], ps = this.match.players.get(event.playerId);
      if (isLearner(ps) && isDraft(event.method) && event.action.t === explicit) event.source = 'policy';
    }
    this.commandEventStart = this.events.length;
  }
  finish(state) {
    this.check(); assert(this.match, 'Recorder must attach first');
    if (this.finished) return this.finished;
    assert(this.match.phase === 'RESULT' && state?.done !== false && state?.terminal !== false, 'Only actual terminal matches can be saved');
    for (const rec of this.battles) assert(rec.result && rec.result.errors === 0, 'Unfinished or errored real battle');
    for (const ps of this.match.players.values()) {
      const terminal = this.keep(observation(this.match, ps));
      let row = this.events.findLast(e => e.playerId === ps.playerId && e.parentSeq === null && e.source !== 'engine');
      if (!row) {
        row = this.keep({ type: 'terminal', seq: this.events.length, playerId: ps.playerId, parentSeq: null,
          source: 'terminal-observer', method: null, round: this.match.round, action: { t: 'terminal' },
          observation: terminal, next_observation: terminal, accepted: false, result: null, reward: null });
        this.events.push(row);
      }
      row.terminated = true; row.terminal_observation = terminal;
    }
    this.finished = writeBundle({ recordDir: this.recordDir, id: this.id, state, recorder: this });
    return this.finished;
  }
}
