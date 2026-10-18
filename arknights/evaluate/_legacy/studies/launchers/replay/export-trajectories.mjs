#!/usr/bin/env node
// Observe the upstream bot's existing validated player methods; do not change decisions or RNG.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';

const opt = {};
for (let i = 2; i < process.argv.length; i += 2) opt[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
const root = path.resolve(opt.game || '../game');
const out = path.resolve(opt.out || '../trajectories');
const web = path.resolve(opt.web || path.join(root, 'public/dev/agent'));
const recordings = path.join(root, 'public/dev/recordings');
const seeds = Number(opt.seeds || 8);
const configs = (opt.configs || 'solo:NORMAL,solo:HARD,solo:ABYSS,coop4:ABYSS').split(',');
const importGame = (f) => import(pathToFileURL(path.join(root, f)));
const { Match } = await importGame('server/match/Match.js');
const { VirtualScheduler } = await importGame('server/match/scheduler.js');
const { getData } = await importGame('server/data.js');
const { Battle } = await importGame('server/sim/Battle.js');
const data = getData({ log: { warn() {}, error() {}, info() {} } });
const SHA = '6ab2468279e926d107788f63d7fe219b85f67b4a';
const copy = (v) => v == null ? null : JSON.parse(JSON.stringify(v));
for (const dir of [out, web, path.join(web, 'episodes'), path.join(web, 'downloads'), recordings]) fs.mkdirSync(dir, { recursive: true });
const index = [];
const recIndexFile = path.join(recordings, 'index.json');
const recIndex = fs.existsSync(recIndexFile) ? JSON.parse(fs.readFileSync(recIndexFile)) : [];

function observation(m, ps) {
  const pub = m.publicView();
  return copy({
    public: pub,
    phase: m.phase, round: m.round, difficulty: m.difficulty, mode: m.mode,
    stageId: m.stageId, disabledBonds: pub.disabledBonds,
    factions: pub.factions, bossId: pub.bossId,
    draft: pub.draft || null, specialChoice: pub.sp || null,
    teamLp: pub.teamLp ?? null, bossHp: pub.bossHp || null,
    teammates: pub.players, own: ps.privateView(),
  });
}

function run(config, seed) {
  const [modeKey, difficulty] = config.split(':');
  const mode = modeKey === 'solo' ? 'solo' : 'coop';
  const players = mode === 'solo' ? 1 : Number(modeKey.replace('coop', ''));
  const id = `${modeKey}-${difficulty}-s${seed}`;
  const events = [], battles = [], errors = [];
  const sched = new VirtualScheduler();
  let summary = null, depth = 0;
  const m = new Match({
    roomCode: 'TRACE', mode, difficulty, seed, data, scheduler: sched, botRehearsal: 0,
    seats: Array.from({ length: players }, (_, seat) => ({ seat, playerId: `ai_${seat}`, name: `AI-${seat + 1}`, isBot: true, connected: true })),
    send: () => true, broadcast() {}, onEnd(s) { summary = copy(s); },
    log: { info() {}, debug() {}, warn() {}, error(...a) { errors.push(a.map(String).join(' ')); } },
  });

  function capture(ps, action, fn, assumedSuccess = false) {
    if (depth) return fn();
    const row = { type: 'transition', seq: events.length, playerId: ps.playerId,
      observation: observation(m, ps), action: copy(action), reward: 0, terminated: false };
    // Reserve its index before any phase transition or nested effect.
    events.push(row);
    depth++;
    let result;
    try { result = fn(); }
    catch (e) { row.exception = String(e.stack || e); throw e; }
    finally { depth--; }
    row.result = copy(result ?? (assumedSuccess ? { ok: true } : null));
    row.accepted = result?.ok === true || (assumedSuccess && !result?.error);
    row.next_observation = observation(m, ps);
    return result;
  }
  const methods = {
    buy: (slot) => ({ t: 'g.buy', slot }), refresh: () => ({ t: 'g.refresh' }),
    freeze: () => ({ t: 'g.freeze' }), levelUp: () => ({ t: 'g.levelUp' }),
    sell: (uid) => ({ t: 'g.sell', uid }), move: (uid, to, dir) => ({ t: 'g.move', uid, to, ...(dir ? { dir } : {}) }),
    equip: (itemUid, targetUid, replaceUid) => ({ t: 'g.equip', itemUid, targetUid, ...(replaceUid != null ? { replaceUid } : {}) }),
    useArt: (itemUid, row, col, dir) => ({ t: 'g.art', itemUid, row, col, ...(dir ? { dir } : {}) }),
    destroy: (uid) => ({ t: 'g.destroy', uid }), pickReward: (idx) => ({ t: 'g.reward', idx }),
    setReady: (ready) => ({ t: 'g.ready', ready }),
  };
  for (const ps of m.players.values()) for (const [method, action] of Object.entries(methods)) {
    const original = ps[method].bind(ps);
    ps[method] = (...args) => capture(ps, action(...args), () => original(...args));
  }
  for (const [method, action] of [
    ['_applyBand', (bandId) => ({ t: 'g.band', bandId })],
    ['_applyCard', (idx) => ({ t: 'g.choice', idx })],
    ['pickPersonalChoice', (idx, choiceId) => ({ t: 'g.choice', idx, choiceId })],
  ]) {
    const original = m[method].bind(m);
    m[method] = (ps, ...args) => capture(ps, action(...args), () => original(ps, ...args), method.startsWith('_apply'));
  }

  // Record genuine battles of one complete ABYSS run. Rehearsals and stat-only previews are excluded.
  if (id === 'solo-ABYSS-s1') {
    m.BattleClass = class RecordedBattle extends Battle {
      constructor(opts) {
        const record = opts.spawns?.length && !opts.fieldId?.startsWith('r:');
        super(record ? { ...opts, recordEvents: true } : opts);
        if (!record) return;
        const name = `agent-${id}-r${m.round}-${battles.length}`;
        this.trace = { version: 1, name, title: `AI 实际对局 · 终极 · R${m.round}`, kind: opts.kind,
          stageId: m.stageId, modeId: m.modeId, round: m.round, tick: 1 / 30,
          field: null, frames: [], battle: this };
        this.tracePending = [];
        this.traceTicks = 0;
        battles.push(this.trace);
      }
      _ev(tuple) {
        super._ev(tuple);
        if (this.trace) this.tracePending.push(copy(tuple));
      }
      traceFrame() {
        if (!this.trace) return;
        if (!this.trace.field) this.trace.field = copy(this.fieldMeta());
        const frame = { snap: copy(this.snapshot()), ev: this.tracePending.splice(0) };
        const last = this.trace.frames.at(-1);
        if (last && last.snap.t === frame.snap.t) { last.ev.push(...frame.ev); last.snap = frame.snap; }
        else this.trace.frames.push(frame);
      }
      step() {
        const result = super.step();
        if (this.trace && (++this.traceTicks % 3 === 0 || this.finished)) this.traceFrame();
        return result;
      }
    };
  }

  const started = performance.now();
  m.start();
  sched.runUntil(() => summary != null, { maxSteps: 5e6 });
  if (!summary) throw new Error(`${id}: did not finish`);
  const lastByPlayer = new Map();
  for (const row of events) lastByPlayer.set(row.playerId, row);
  for (const [pid, row] of lastByPlayer) {
    row.terminated = true;
    row.reward = summary.players?.find(p => p.playerId === pid)?.victory ? 1 : -1;
    row.next_observation = observation(m, m.players.get(pid));
  }
  const replayRefs = [];
  for (const rec of battles) {
    const b = rec.battle;
    b.traceFrame();
    delete rec.battle;
    rec.duration = b.time;
    rec.result = copy(b.result());
    const filename = `${rec.name}.json`;
    fs.writeFileSync(path.join(recordings, filename), JSON.stringify(rec));
    const meta = { name: rec.name, title: rec.title, file: filename, kind: rec.kind, stageId: rec.stageId,
      round: rec.round, duration: rec.duration };
    recIndex.push(meta); replayRefs.push(meta);
  }
  const counts = {};
  for (const row of events) counts[row.action.t] = (counts[row.action.t] || 0) + 1;
  const meta = { id, config, seed, players, commit: SHA, source: 'upstream-heuristic-bot', rehearsal: 0,
    schema: 'arknights.bot-transitions.v1', observation_scope: 'own-private-plus-public-view',
    note: 'Method-level observations of upstream bot. No legal-action masks or per-battle reward shaping. Failure data is retained. Not a trained expert.',
    victory: summary.victory, roundsPassed: summary.roundsPassed, hiddenReached: summary.hiddenReached,
    steps: events.length, accepted: events.filter(e => e.accepted).length,
    engineErrors: errors.length + m.errorCount, schedulerErrors: sched.errors.length,
    wallMs: performance.now() - started, actions: counts, battles: replayRefs,
    terminalReason: summary.reason, summary };
  const raw = [{ type: 'episode', ...meta }, ...events, { type: 'terminal', summary }].map(r => JSON.stringify(r)).join('\n') + '\n';
  const compressed = gzipSync(raw);
  fs.writeFileSync(path.join(out, `${id}.jsonl.gz`), compressed);
  fs.copyFileSync(path.join(out, `${id}.jsonl.gz`), path.join(web, 'downloads', `${id}.jsonl.gz`));
  const viewer = { meta, steps: events.map(r => ({ seq: r.seq, playerId: r.playerId, action: r.action,
    accepted: r.accepted, result: r.result, reward: r.reward, terminated: r.terminated,
    observation: r.next_observation, before: { phase: r.observation.phase, round: r.observation.round,
      funds: r.observation.own.funds, lp: r.observation.own.lp } })) };
  fs.writeFileSync(path.join(web, 'episodes', `${id}.json`), JSON.stringify(viewer));
  const transitionHash = createHash('sha256').update(events.map(e => JSON.stringify(e)).join('\n')).digest('hex');
  index.push({ ...meta, summary: undefined, bytes: compressed.length, transitionHash, file: `episodes/${id}.json` });
  m.dispose();
  console.log(`${id}: ${events.length} actions (${meta.accepted} accepted), ${summary.victory ? 'WIN' : 'loss'} R${summary.roundsPassed}, ${Math.round(meta.wallMs)} ms`);
}

for (const config of configs) for (let seed = 1; seed <= seeds; seed++) run(config, seed);
const manifest = { schema: 'arknights.bot-transitions.v1', createdAt: new Date().toISOString(), commit: SHA,
  episodes: index, totalSteps: index.reduce((n, e) => n + e.steps, 0),
  totalBytes: index.reduce((n, e) => n + e.bytes, 0),
  quality: '规则 bot 原始轨迹，含失败；尚无训练模型或专家保证。终局奖励仅写在每座位最后一次决策，不含完整合法动作掩码。' };
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(manifest, null, 2));
fs.writeFileSync(path.join(web, 'index.json'), JSON.stringify(manifest));
fs.writeFileSync(recIndexFile, JSON.stringify([...new Map(recIndex.map(r => [r.name, r])).values()]));
console.log(`Saved ${index.length} episodes / ${manifest.totalSteps} decisions / ${(manifest.totalBytes / 1048576).toFixed(1)} MiB gzip`);
