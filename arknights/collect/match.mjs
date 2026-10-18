// Sanctioned MatchClass extension. RealScheduler and all game decisions stay upstream.
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { redact } from './projection.mjs';
const methods = {
  buy: slot => ({ t: 'g.buy', slot }), refresh: () => ({ t: 'g.refresh' }), freeze: () => ({ t: 'g.freeze' }),
  levelUp: () => ({ t: 'g.levelUp' }), sell: uid => ({ t: 'g.sell', uid }),
  move: (uid, to, dir) => ({ t: 'g.move', uid, to, dir }), equip: (itemUid, targetUid, replaceUid) => ({ t: 'g.equip', itemUid, targetUid, replaceUid }),
  useArt: (itemUid, row, col, dir) => ({ t: 'g.art', itemUid, row, col, dir }), destroy: uid => ({ t: 'g.destroy', uid }),
  pickReward: idx => ({ t: 'g.reward', idx }), setReady: ready => ({ t: 'g.ready', ready }), resolveTemp: () => ({ t: 'resolveTemp' }),
};
export function capturedMatchClass(Match, Battle, collector) {
  return class CapturedMatch extends Match {
    constructor(opts) {
      const enrolled = opts.seats.filter(s => !s.isBot).map(s => collector.players.get(s.playerId));
      const participating = enrolled.filter(Boolean);
      if (!participating.length) { super(opts); return; }
      if (enrolled.some(p => !p || p.collectionId !== participating[0].collectionId)) throw Error('All humans must consent to the same collection before start');
      const collectionId = participating[0].collectionId, id = 'm-' + randomUUID();
      const store = collector.store;
      const seats = opts.seats.map(s => ({ seat: s.seat, playerId: s.playerId, isBot: s.isBot,
        participantId: collector.players.get(s.playerId)?.id || null }));
      store.match(collectionId, { id, roomCode: opts.roomCode, matchNo: opts.matchNo, seats,
        mode: opts.mode, difficulty: opts.difficulty, cs: opts.cs, started: Date.now() });
      store.append(collectionId, 'verification', { kind: 'rules', matchId: id, seed: opts.seed });
      super({ ...opts, clientCombat: false, onEnd: outcome => {
        try { store.finish(collectionId, id, outcome); }
        catch (e) { store.failed.set(collectionId, 'terminal capture failed'); }
        opts.onEnd(outcome);
      } });
      Object.assign(this, { captureId: id, collectionId, captureStore: store, collector });
      collector.matches.set(id, this);
      for (const seat of seats) collector.active.set(seat.playerId, this);
      const audit = row => store.append(collectionId, 'verification', { matchId: id, ...row });
      const wrap = (object, method, invoke) => {
        if (typeof object[method] !== 'function') return;
        const fn = object[method]; object[method] = (...args) => invoke(fn, args);
      };
      for (const ps of this.players.values()) for (const [method, action] of Object.entries(methods)) {
        wrap(ps, method, (fn, args) => {
          const result = fn.apply(ps, args);
          audit({ kind: 'operation', playerId: ps.playerId, seat: ps.seat, action: action(...args),
            source: method === 'resolveTemp' ? 'engine' : this.captureContext?.playerId === ps.playerId && this.captureContext?.action === action(...args).t ? 'human' :
              ps.isBot ? 'bot' : ps.autoplay ? 'autoplay' : 'timer',
            requestId: this.captureContext?.requestId || null, result,
            status: result?.error ? 'rejected' : result?.ok ? 'accepted' : 'unknown' });
          return result;
        });
      }
      for (const name of ['_applyBand', '_applyCard', 'pickPersonalChoice', 'reportError', 'onDisconnect', 'onReconnect', 'onLeave']) {
        wrap(this, name, (fn, args) => {
          const ps = args[0], result = fn.apply(this, args);
          audit({ kind: name, playerId: typeof ps === 'string' ? ps : ps?.playerId,
            source: this.captureContext ? 'human' : ps?.isBot ? 'bot' : ps?.autoplay ? 'autoplay' : 'timer',
            args: name === 'reportError' ? [String(args[0])] : args.slice(1), result });
          if (name === 'reportError') collector.store.failed.set(collectionId, 'engine reported error');
          return result;
        });
      }
      this.installBattles(Battle, audit);
    }
    handle(playerId, message) {
      if (!this.captureId) return super.handle(playerId, message);
      const incoming = this.collector.takeRequest(playerId, message);
      const { _arkCollect, ...cleanMessage } = message;
      this.captureContext = { playerId, action: message.t, requestId: incoming?.requestId || null };
      try {
        const ps = this.players.get(playerId);
        const idempotent = (message.t === 'g.infoReady' && ps?.infoReady) ||
          (message.t === 'g.autoplay' && ps?.autoplay === message.on) ||
          (message.t === 'g.ready' && ps?.ready === message.ready) ||
          (message.t === 'g.pause' && this.paused === message.on);
        const result = super.handle(playerId, cleanMessage);
        // In server combat reports have no execution/authority. OK alone is not a state-change label.
        const status = result?.error ? 'rejected' : message.t.startsWith('b.') || idempotent ? 'ignored' : 'accepted';
        this.captureStore.append(this.collectionId, 'verification', { kind: 'handled', matchId: this.captureId,
          playerId, requestId: incoming?.requestId || null, epoch: incoming?.epoch || null, action: cleanMessage, status, result,
          round: this.round, phase: this.phase });
        return result;
      } finally { this.captureContext = null; }
    }
    installBattles(Battle, audit) {
      const match = this, store = this.captureStore; let preview = 0, number = 0;
      const construct = this.newBattle;
      this.newBattle = function(opts, options) { if (options?.preview) preview++;
        try { return construct.call(this, opts, options); } finally { if (options?.preview) preview--; } };
      this.BattleClass = class CaptureBattle extends Battle {
        constructor(opts) {
          const capture = !preview && !opts.fieldId?.startsWith('r:');
          super(capture ? { ...opts, recordEvents: true } : opts);
          this.collectEnabled = capture; this.pendingCollect ||= [];
          if (!capture) return;
          this.collectRecord = { version: 1, name: `${match.captureId}-r${match.round}-${number++}`,
            title: '真人采集 · recorded-events', kind: opts.kind, stageId: match.stageId, modeId: match.modeId,
            round: match.round, tick: this.dt, participants: opts.players.map(p => p.playerId), frameEvery: 3, field: null, frames: [] };
          (match.collectBattles ||= []).push(this.collectRecord);
        }
        _ev(tuple) { super._ev(tuple);
          if (!store.failed.has(match.collectionId) && this.collectEnabled !== false && !preview && !this.fieldId?.startsWith('r:')) (this.pendingCollect ||= []).push(redact(tuple)); }
        captureFrame() {
          if (!this.collectRecord || store.failed.has(match.collectionId)) { this.pendingCollect.length = 0; return; }
          try {
            const rec = this.collectRecord; rec.field ||= redact(this.fieldMeta());
            const last = rec.frames.at(-1), snap = redact(this.snapshot()), ev = this.pendingCollect.splice(0);
            if (last?.snap.t === snap.t) { last.snap = snap; last.ev.push(...ev); } else rec.frames.push({ snap, ev });
            if (this.finished && !rec.result) {
              rec.duration = this.time; rec.result = redact(this.result());
              const dir = store.file(match.collectionId, 'battles'); fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
              fs.writeFileSync(path.join(dir, rec.name + '.json'), JSON.stringify(rec), { flag: 'wx', mode: 0o600 });
              audit({ kind: 'battle', name: rec.name, round: rec.round, result: rec.result });
              // Frames are on disk; do not retain an entire multi-hour game in RAM.
              rec.frames = [];
            }
          } catch (e) { store.failed.set(match.collectionId, 'battle recording failed: ' + e.message); }
        }
        start() { const r = super.start(); this.captureFrame(); return r; }
        step() { const r = super.step(); if (this.tickCount % 3 === 0 || this.finished) this.captureFrame(); return r; }
        forceEnd(reason) { const r = super.forceEnd(reason); this.captureFrame(); return r; }
      };
    }
    dispose() {
      if (this.disposed) return;
      if (this.captureId) {
        try { this.captureStore.finish(this.collectionId, this.captureId, null, this.captureStore.failed.get(this.collectionId)); }
        catch { this.captureStore.failed.set(this.collectionId, 'partial capture could not be sealed'); }
        finally {
          this.collector.matches.delete(this.captureId);
          const archived = { captureId: this.captureId, collectionId: this.collectionId,
            players: new Map([...this.players.values()].map(p => [p.playerId, { seat: p.seat }])) };
          for (const playerId of this.players.keys()) if (this.collector.active.get(playerId) === this) this.collector.active.set(playerId, archived);
        }
      }
      return super.dispose();
    }
  };
}
