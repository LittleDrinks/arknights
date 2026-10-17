import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { RULES } from '../rl/env.mjs';
import { CONTROLLER, validateTrace, verifyCandidate, verifyState, verifyTerminal } from './replay.mjs';
import { Recorder, viewerIds } from './record.mjs';
import { mergeIndexes } from './index.mjs';

// These are deliberately small contract fixtures, not claimed to be model evaluations.
const fixture = () => ({ rules: RULES, controller: CONTROLLER, budget: 8,
  result: { seed: 1, mode: 'solo', difficulty: 'ABYSS', budget: 8, victory: false,
    rounds: 3, steps: 1, engineErrors: 0, actionErrors: 0, controllerErrors: 0,
    budgetForcedReady: 0, controllerBlockedSales: 0 },
  actions: [{ round: 1, candidateIndex: 0, action: { type: 'sell', uid: 7, id: 'chess_a', price: 1 } }] });

test('strict trace/config/terminal validation and complete UID/slot candidate binding', () => {
  const t = fixture(); validateTrace(t);
  for (const mutate of [x => x.rules = 'wrong', x => x.controller = 'wrong',
    x => x.budget++, x => x.result.seed = 0, x => x.result.budget = 0,
    x => delete x.result.controllerErrors, x => x.actions.pop()]) {
    const bad = structuredClone(t); mutate(bad); assert.throws(() => validateTrace(bad));
  }
  const candidate = t.actions[0];
  const s = { done: false, rounds: 1, candidates: [candidate.action] };
  verifyCandidate(s, candidate, 0);
  for (const [key, value] of [['uid', 8], ['id', 'wrong'], ['price', 2], ['slot', 1]]) {
    const bad = structuredClone(candidate); bad.action[key] = value;
    assert.throws(() => verifyCandidate(s, bad, 0), /binding mismatch/);
  }
  assert.throws(() => verifyCandidate(s, { ...candidate, candidateIndex: 1 }, 0), /out of range/);
  assert.throws(() => verifyCandidate({ ...s, done: true }, candidate, 0), /past terminal/);
  const state = { ...t.result, rules: RULES, controller: CONTROLLER, done: true, reward: -1,
    episode: { seed: 1, mode: 'solo', difficulty: 'ABYSS', budget: 8 },
    summary: { victory: false, roundsPassed: 3 } };
  verifyState(state, t); verifyTerminal(state, t);
  for (const key of ['victory', 'rounds', 'steps', 'budgetForcedReady', 'controllerBlockedSales']) {
    const bad = structuredClone(state); bad[key] = key === 'victory' ? true : bad[key] + 1;
    assert.throws(() => verifyTerminal(bad, t));
  }
  assert.throws(() => verifyTerminal({ ...state, done: false }, t), /before terminal/);
  assert.throws(() => verifyState({ ...state, controller: 'changed' }, t), /controller drift/);
  assert.throws(() => verifyState({ ...state, episode: { ...state.episode, budget: 9 } }, t), /configuration drift/);
});

function fakeMatch() {
  const ps = { playerId: 'learner', funds: 4, ready: false,
    privateView() { return { funds: this.funds, lp: 10 }; } };
  for (const method of ['buy', 'sell', 'refresh', 'freeze', 'levelUp', 'move', 'equip',
    'useArt', 'destroy', 'pickReward', 'setReady', 'resolveTemp']) ps[method] = () => ({ ok: true });
  ps.buy = () => { ps.funds--; return { ok: true }; };
  const m = { phase: 'BAND_DRAFT', round: 0, players: new Map([['learner', ps]]),
    publicView() { return { players: [{ playerId: ps.playerId }] }; },
    _applyBand() {}, _applyCard() {}, pickPersonalChoice: () => ({ ok: true }),
    newBattle(opts) { return new this.BattleClass(opts); } };
  return { ps, m };
}

test('constructor hooks survive env wrappers, blocked-sale replacement and nested real methods', () => {
  const { ps, m } = fakeMatch();
  const r = new Recorder({ id: 'test' }); r.env = { source: 'fixed-controller' };
  r.attach(m, class {});
  m._applyBand(ps, 'band'); // Before any reset advance / model step.
  assert.equal(r.events[0].observation.phase, 'BAND_DRAFT');
  const original = ps.buy.bind(ps);
  ps.buy = (...args) => original(...args); // ShopEnv's history decorator.
  r.env.source = 'policy'; ps.buy(2);
  assert.equal(r.events.filter(e => e.method === 'buy').length, 1);
  assert.equal(r.events[1].observation.own.funds, 4);
  assert.equal(r.events[1].next_observation.own.funds, 3);
  const sell = ps.sell;
  r.env.source = 'fixed-controller';
  ps.sell = () => ({ ok: false, error: 'RL_CONTROLLER_NO_SALES' });
  ps.sell(7); ps.sell = sell;
  assert.equal(r.events.at(-1).accepted, false);
  assert.equal(r.events.at(-1).source, 'fixed-controller');
  ps.resolveTemp(); assert.equal(r.events.at(-1).source, 'budget-controller');
  r.env.source = 'policy'; ps.ready = true;
  ps.resolveTemp(); assert.equal(r.events.at(-1).source, 'engine');
  ps.setReady = () => { ps.resolveTemp(); return { ok: true }; };
  ps.setReady(true);
  assert.equal(r.events.at(-1).parentSeq, r.events.at(-2).seq);
  assert.equal(r.events.at(-1).method, 'resolveTemp');
});

test('real-battle hook contract retains constructor events, zero-spawn battles and final tick; excludes previews', () => {
  const { m } = fakeMatch();
  class Battle {
    constructor(opts) { this.fieldId = opts.fieldId; this.recordEvents = opts.recordEvents;
      this.dt = 1 / 30; this.tickCount = 0; this.time = 0; this._ev(['constructor']); }
    _ev() {}
    start() { this._ev(['start']); }
    step() { this.tickCount++; this.time = this.tickCount / 30; this.finished = this.tickCount === 4; }
    fieldMeta() { return { fieldId: this.fieldId }; }
    snapshot() { return { fieldId: this.fieldId, t: this.time, units: [] }; }
  }
  const r = new Recorder({ id: 'test' }); r.env = {}; r.attach(m, Battle);
  m.newBattle({ fieldId: 'n:learner' }, { preview: true });
  m.newBattle({ fieldId: 'r:learner' });
  const b = m.newBattle({ fieldId: 'n:learner', spawns: [] });
  b.start(); for (let i = 0; i < 4; i++) b.step();
  assert.equal(r.battles.length, 1);
  assert.deepEqual(b.recording.frames.map(f => f.snap.t), [0, 3 / 30, 4 / 30]);
  assert.deepEqual(b.recording.frames[0].ev, [['constructor'], ['start']]);
  assert.deepEqual(viewerIds({ perPlayer: { learner: 1 }, fieldId: 'n:learner', uid: 7 }),
    { perPlayer: { ai_0: 1 }, fieldId: 'n:ai_0', uid: 7 });
});

test('pure index append preserves inputs, rejects duplicate IDs/files and dangling battle references', () => {
  const old = { schema: 'schema', episodes: [{ id: 'old', file: 'episodes/old.json', steps: 1, bytes: 3 }] };
  const added = { schema: 'schema', episodes: [{ id: 'new', file: 'episodes/new.json', steps: 2,
    bytes: 4, battles: [{ name: 'battle', file: 'battle.json' }] }] };
  const rec = [{ name: 'battle', file: 'battle.json' }];
  const before = JSON.stringify([old, added, rec]);
  const result = mergeIndexes(old, [], added, rec);
  assert.equal(result.agent.totalSteps, 3); assert.equal(result.agent.totalBytes, 7);
  assert.equal(JSON.stringify([old, added, rec]), before);
  assert.throws(() => mergeIndexes(old, [], old, []), /collision/);
  assert.throws(() => mergeIndexes(old, [], added, []), /Missing recording/);
  assert.throws(() => mergeIndexes(old, rec, added, rec), /collision/);
  const bad = structuredClone(added); bad.episodes[0].file = '../escape.json';
  assert.throws(() => mergeIndexes(old, [], bad, rec), /Unsafe/);
});

const tracePath = process.env.REPLAY_TRACE;
const checkpoint = process.env.REPLAY_CHECKPOINT;
test('existing learner eval: complete bounded CLI replay, native UI consumer, no overwrite',
  { skip: !tracePath || !checkpoint, timeout: 25000 }, async () => {
    const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'model-replay-verified-'));
    const out = path.join(parent, 'bundle');
    const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
    const args = [cli, '--trace', tracePath, '--checkpoint', checkpoint, '--out', out];
    const run = spawnSync(process.execPath, args, { encoding: 'utf8', timeout: 25000 });
    assert.equal(run.status, 0, run.stderr);
    const report = JSON.parse(run.stdout);
    assert(report.totalBytes < 20_000_000 && report.peakRssBytes < 768 * 1024 * 1024);
    assert(report.simulationMs < 25000);
    const read = name => JSON.parse(fs.readFileSync(path.join(out, name)));
    const manifest = read('agent/index.json'), episode = read(`agent/${manifest.episodes[0].file}`);
    assert.equal(episode.meta.steps, episode.steps.length);
    const trace = JSON.parse(fs.readFileSync(tracePath));
    assert.deepEqual(report.result, trace.result);
    assert.equal(episode.meta.summary.victory, trace.result.victory);
    assert.equal(episode.steps.at(-1).action.t, 'g.ready');
    assert.equal(episode.steps.at(-1).terminated, true);
    assert.equal(episode.engineCalls.length + episode.steps.length, report.methodCalls);
    const policies = episode.steps.filter(s => s.policy);
    assert.equal(policies.length, trace.actions.length);
    policies.forEach((s, i) => assert.deepEqual(s.policy.candidate, trace.actions[i].action));
    const jsonl = gunzipSync(fs.readFileSync(path.join(out, 'agent/downloads', `${report.id}.jsonl.gz`)))
      .toString().trim().split('\n').map(line => JSON.parse(line));
    assert.equal(jsonl[0].type, 'episode'); assert.equal(jsonl.at(-1).type, 'terminal');
    assert.equal(jsonl.filter(r => r.type === 'transition').length, report.methodCalls);
    for (const f of report.files) {
      const bytes = fs.readFileSync(path.join(out, f.file));
      assert.equal(bytes.length, f.bytes);
      assert.equal(createHash('sha256').update(bytes).digest('hex'), f.sha256);
    }
    // Execute the ORIGINAL consumer's functions with only DOM/network/store dependencies stubbed.
    // This checks its real fixed URLs, ai_0 filter, round selection, frame messages, seek and ending.
    const game = process.env.STRONGHOLD_GAME || fileURLToPath(new URL('../../Stronghold-Protocol', import.meta.url));
    const source = fs.readFileSync(path.join(game, 'public/js/agentReplay.js'), 'utf8');
    const elements = new Map(), emitted = [], requested = [], raf = [];
    const control = name => {
      if (!elements.has(name)) elements.set(name, { value: '', textContent: '',
        replaceChildren(...children) { this.children = children; this.value = children[0]?.value || ''; } });
      return elements.get(name);
    };
    let storeValue = {};
    const context = vm.createContext({ URL, performance, structuredClone, console,
      t: s => s, emptyMatch: () => ({}),
      Option: class { constructor(text, value) { this.text = text; this.value = value; } },
      location: { href: 'http://local/?replay=test' }, history: { replaceState() {} },
      requestAnimationFrame: fn => raf.push(fn),
      fetch: async url => { requested.push(url); assert(url.startsWith('/dev/'));
        return { ok: true, json: async () => read(url.slice('/dev/'.length)) }; },
      store: { set(v) { storeValue = v; }, get: () => storeValue,
        patch(k, v) { storeValue[k] = { ...storeValue[k], ...v }; } },
      net: { _emit: (type, msg) => emitted.push({ type, msg }) },
      testControls: { querySelector: selector => control(selector.match(/"([^"]+)"/)[1]) },
    });
    vm.runInContext(source.replace(/^import .*;\n/gm, '').replace('export async function bootReplay', 'async function bootReplay')
      + '\ncontrols = testControls; globalThis.api = { state, loadEpisode, showStep, showBattle, seekBattle, pushFrame, tick };', context);
    const api = context.api;
    api.state.manifest = manifest;
    await api.loadEpisode(report.id);
    assert.equal(api.state.steps.length, episode.steps.length);
    assert.equal(storeValue.me.playerId, 'ai_0');
    for (const b of episode.meta.battles) {
      api.showStep(episode.steps.findIndex(s => s.before.round === b.round && s.action.t === 'g.ready'));
      await api.showBattle();
      const rec = api.state.recording;
      assert.equal(storeValue.match.field.fieldId, rec.field.fieldId);
      for (const frame of rec.frames) api.pushFrame(frame);
      api.seekBattle(rec.frames.length - 1); raf.pop()();
      assert.equal(emitted.at(-1).msg.t, 'b.snap');
      assert.equal(emitted.at(-1).msg.gt, rec.frames.at(-1).snap.t);
      assert.equal(emitted.at(-1).msg.fieldId, rec.field.fieldId);
      const seekSpawns = emitted.at(-2);
      if (seekSpawns.type === 'b.ev') assert(seekSpawns.msg.ev.every(e => e[0] === 'spawn'));
    }
    assert(emitted.some(e => e.type === 'b.ev' && e.msg.ev.length));
    api.state.playing = true; api.state.frameIndex = api.state.recording.frames.length;
    await api.tick(performance.now());
    assert.equal(storeValue.match.result.victory, trace.result.victory);
    assert.equal(requested.length, 1 + report.battles);
    // A second invocation refuses the existing output before running the simulator.
    const again = spawnSync(process.execPath, args, { encoding: 'utf8', timeout: 5000 });
    assert.notEqual(again.status, 0); assert.match(again.stderr, /already exists/);
    for (const f of report.files) assert.equal(createHash('sha256').update(fs.readFileSync(path.join(out, f.file))).digest('hex'), f.sha256);
    console.log(JSON.stringify({ out, simulationMs: report.simulationMs, peakRssBytes: report.peakRssBytes,
      totalBytes: report.totalBytes, policySteps: report.policySteps, methodCalls: report.methodCalls,
      battles: report.battles, frames: report.frames, battleEvents: report.battleEvents, victory: report.victory,
      nativeUiConsumer: 'passed; no browser rendering claimed' }));
  });
