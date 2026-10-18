import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { RULES } from '../rl/env.mjs';
import { MANIFEST, MANIFEST_SHA256, selectTeam } from '../coop/pool.mjs';
import { KIND, CONTROLLER, configuration, validateTrace, verifyCandidate, verifyState, verifyTerminal } from './verify.mjs';
import { Recorder, PLAYER_MAP, viewerIds } from './record.mjs';
import { bundle, mergeIndexes } from './export.mjs';

function traceFixture() {
  const config = { pool: 'normal', split: 'train', poolSeed: 313, fixed: null };
  const terminal = { ok: true, rules: RULES, controller: CONTROLLER, episode: { seed: 50003,
    mode: 'coop', difficulty: 'ABYSS', budget: 128, partners: {
      manifest: MANIFEST.version, manifestSHA256: MANIFEST_SHA256, ...config,
      seats: selectTeam(config, 50003).map((p, i) => ({ seat: i + 1, id: p.id, family: p.family })) } },
    done: true, victory: true, rounds: 14, steps: 1, reward: 1, engineErrors: 0, actionErrors: 0, controllerErrors: 0,
    summary: { victory: true, roundsPassed: 14, teamLp: 4, errors: 0, players: [{ alive: false }, {}, {}, {}] },
    partnerAudit: Object.fromEntries([1, 2, 3].map(i => [`partner-${i}`, { actionSHA256: String(i).repeat(64) }])) };
  return { kind: KIND, rules: RULES, controller: CONTROLLER, budget: 128, terminal,
    actions: [{ round: 1, candidateIndex: 0, action: { type: 'buy', slot: 2, id: 'chess', price: 2,
      tier: 1, golden: false, bonds: ['a', 'b'] } }] };
}

test('trace config is explicit; full candidates, terminal and partner SHA fail closed', () => {
  const t = traceFixture(); validateTrace(t);
  assert.deepEqual(configuration(t), { pool: 'normal', split: 'train', poolSeed: 313, fixed: null });
  const { ok, ...state } = structuredClone(t.terminal);
  verifyState(state, t); verifyTerminal(state, t);
  for (const mutate of [x => x.rules = 'wrong', x => x.kind = 'frozen-eval', x => x.budget++,
    x => x.terminal.episode.partners.poolSeed = 0, x => x.terminal.episode.partners.fixed = [],
    x => x.terminal.episode.partners.manifestSHA256 = 'wrong', x => x.terminal.episode.partners.seats[0].id = 'wrong',
    x => x.terminal.actionErrors++, x => x.terminal.engineErrors++, x => x.actions.pop()]) {
    const bad = structuredClone(t); mutate(bad); assert.throws(() => validateTrace(bad));
  }
  for (const mutate of [s => s.done = false, s => s.summary.teamLp++, s => s.summary.players[0].alive = true,
    s => s.partnerAudit['partner-1'].actionSHA256 = '0'.repeat(64), s => s.extra = 1]) {
    const bad = structuredClone(state); mutate(bad); assert.throws(() => verifyTerminal(bad, t));
  }
  const entry = t.actions[0], current = { done: false, rounds: 1, candidates: [entry.action] };
  verifyCandidate(current, entry, 0);
  for (const [k, v] of Object.entries({ slot: 3, id: 'different', price: 3, tier: 2, golden: true, bonds: ['b', 'a'] })) {
    const bad = structuredClone(entry); bad.action[k] = v;
    assert.throws(() => verifyCandidate(current, bad, 0));
  }
  assert.throws(() => verifyCandidate({ ...current, rounds: 2 }, entry, 0));
  assert.throws(() => verifyCandidate({ ...current, done: true }, entry, 0));
});

function fakeMatch() {
  const players = new Map(Object.keys(PLAYER_MAP).map((playerId, seat) => {
    const ps = { playerId, seat, ready: false, alive: true, funds: 4,
      privateView() { return { playerId, funds: this.funds, lp: this.alive ? 2 : 0, alive: this.alive, ready: this.ready }; } };
    for (const method of ['buy', 'sell', 'refresh', 'freeze', 'levelUp', 'move', 'equip',
      'useArt', 'destroy', 'pickReward', 'setReady', 'resolveTemp']) ps[method] = () => ({ ok: true });
    ps.buy = () => { ps.funds--; return { ok: true }; };
    ps.setReady = ready => { ps.ready = ready; ps.resolveTemp(); return { ok: true }; };
    return [playerId, ps];
  }));
  return { players, phase: 'BAND_DRAFT', round: 0, difficulty: 'ABYSS', mode: 'coop', stageId: 'test-stage',
    publicView() { return { players: [...players.values()].map(p => ({ playerId: p.playerId, seat: p.seat, alive: p.alive })) }; },
    _applyBand() {}, _applyCard() {}, pickPersonalChoice: () => ({ ok: true }),
    newBattle(opts) { return new this.BattleClass(opts); } };
}

class FakeBattle {
  constructor(opts) { this.fieldId = opts.fieldId; this.recordEvents = opts.recordEvents !== false;
    this.dt = 1 / 30; this.time = 0; this.tickCount = 0;
    this._ev(['spawn', { id: 7, playerId: opts.players[0].playerId }]); }
  _ev() {}
  start() { this._ev(['start']); }
  step() { this.tickCount++; this.time = this.tickCount / 30; this._ev(['tick', this.tickCount]); this.finished = this.tickCount === 4; }
  fieldMeta() { return { fieldId: this.fieldId, rect: {}, units: [] }; }
  snapshot() { return { fieldId: this.fieldId, t: this.time, units: [[7]], dps: { 'partner-1': 5 } }; }
  result() { return { errors: 0, perPlayer: { 'partner-1': { kills: 1 } } }; }
}

test('seat attribution, nested wrappers, blocked sales, preview exclusion and budget latch', () => {
  const m = fakeMatch(), r = new Recorder({ id: 'test', record: true });
  r.env = { source: 'policy' }; r.attach(m, FakeBattle);
  const learner = m.players.get('learner'), partner = m.players.get('partner-1');
  const original = partner.buy.bind(partner); partner.buy = (...args) => original(...args);
  partner.buy(1); learner.buy(2);
  assert.deepEqual(r.events.map(e => e.source), ['partner-controller', 'policy']);
  assert.equal(r.events[0].next_observation.own.funds, 3);
  assert.equal(r.events[0].observation.own.funds, 4);
  learner.setReady(true);
  assert.equal(r.events.at(-1).source, 'engine');
  assert.equal(r.events.at(-1).parentSeq, r.events.at(-2).seq);
  const sell = learner.sell;
  r.env.source = 'fixed-controller';
  learner.sell = () => ({ ok: false, error: 'RL_CONTROLLER_NO_SALES' });
  learner.sell(7); learner.sell = sell;
  assert.equal(r.events.at(-1).accepted, false);
  const preview = m.newBattle({ fieldId: 'n:learner', players: [learner] }, { preview: true });
  preview.start(); preview.step();
  assert.equal(r.battles.length, 0); assert.equal(preview.pending.length, 0);
  const b = m.newBattle({ fieldId: 'n:partner-1', kind: 'normal', players: [partner] });
  b.start(); for (let i = 0; i < 4; i++) b.step();
  assert.deepEqual(b.recording.frames.map(f => f.snap.t), [0, 0.1, 4 / 30]);
  assert.equal(b.recording.frames.flatMap(f => f.ev).length, 6);
  assert.deepEqual(viewerIds({ fieldId: 'n:partner-3', dps: { learner: 1, 'partner-1': 2 }, uid: 7 }),
    { fieldId: 'n:ai_3', dps: { ai_0: 1, ai_1: 2 }, uid: 7 });
  r.maxBytes = 1;
  assert.throws(() => r.check({ too: 'large' }), /budget/);
  r.maxBytes = Infinity; assert.throws(() => r.check(), /budget/);
});

function mockBundle() {
  const m = fakeMatch(), recorder = new Recorder({ id: 'fixture', record: true });
  const env = { m, source: 'policy', summary: { victory: true, teamLp: 4, players: [] } };
  recorder.env = env; recorder.attach(m, FakeBattle);
  for (const ps of m.players.values()) m._applyBand(ps, 'band');
  m.phase = 'PREP'; m.round = 1;
  const battle = (fieldId, participants, kind = 'normal') => {
    const b = m.newBattle({ fieldId, players: participants, kind }); b.start();
    for (let i = 0; i < 4; i++) b.step();
  };
  for (const ps of m.players.values()) { ps.buy(2); ps.setReady(true); battle(`n:${ps.playerId}`, [ps]); }
  battle('u', [...m.players.values()], 'unite');
  m.round = 14;
  m.players.get('learner').alive = false; m.players.get('partner-3').alive = false;
  const alive = [...m.players.values()].filter(p => p.alive);
  for (const ps of alive) { ps.ready = false; ps.buy(2); ps.setReady(true); }
  battle('b1', alive, 'boss'); m.phase = 'RESULT';
  env.summary.players = [...m.players.values()].map(p => ({ playerId: p.playerId, alive: p.alive }));
  const recordings = recorder.finish(env);
  const state = { rules: RULES, episode: { difficulty: 'ABYSS', seed: 50003 }, steps: 1, victory: true,
    rounds: 14, engineErrors: 0, summary: { ...env.summary, reason: 'victory' } };
  return bundle({ id: 'fixture', state, recorder, recordings, provenance: { kind: KIND }, maxBytes: 2_000_000 });
}

test('native UI functions consume all four seats and all fields, seek, and show team victory with dead seats', async () => {
  const files = mockBundle(), read = name => JSON.parse(files.get(name));
  const manifest = read('agent/index.json'), recs = read('recordings/index.json');
  assert.equal(recs.length, 6);
  const merged = mergeIndexes({ schema: manifest.schema, episodes: [] }, [], manifest, recs);
  assert.equal(merged.agent.episodes.length, manifest.episodes.length);
  assert.throws(() => mergeIndexes(manifest, recs, manifest, recs), /collision/);
  assert.throws(() => mergeIndexes({ schema: manifest.schema, episodes: [] }, [], manifest, []), /Missing recording/);
  const bad = structuredClone(manifest); bad.episodes[0].file = '../outside';
  assert.throws(() => mergeIndexes({ schema: manifest.schema, episodes: [] }, [], bad, recs), /Unsafe/);
  const elements = new Map(), emitted = [], requested = [], raf = [];
  const control = name => {
    if (!elements.has(name)) elements.set(name, { value: '', textContent: '',
      replaceChildren(...children) { this.children = children; this.value = children[0]?.value || ''; } });
    return elements.get(name);
  };
  let storeValue = {};
  const context = vm.createContext({ URL, performance, structuredClone, console, t: s => s, emptyMatch: () => ({}),
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    location: { href: 'http://local/?replay=fixture' }, history: { replaceState() {} }, requestAnimationFrame: fn => raf.push(fn),
    fetch: async url => { requested.push(url); assert(url.startsWith('/dev/')); return { ok: true, json: async () => read(url.slice(5)) }; },
    store: { set(v) { storeValue = v; }, get: () => storeValue, patch(k, v) { storeValue[k] = { ...storeValue[k], ...v }; } },
    net: { _emit: (type, msg) => emitted.push({ type, msg }) },
    testControls: { querySelector: selector => control(selector.match(/"([^"]+)"/)[1]) } });
  const game = process.env.STRONGHOLD_GAME || fileURLToPath(new URL('../../Stronghold-Protocol', import.meta.url));
  const source = fs.readFileSync(path.join(game, 'public/js/agentReplay.js'), 'utf8');
  vm.runInContext(source.replace(/^import .*;\n/gm, '').replace('export async function bootReplay', 'async function bootReplay')
    + '\ncontrols = testControls; globalThis.api = { state, loadEpisode, selectPlayer, showStep, showBattle, seekBattle, pushFrame, tick };', context);
  const api = context.api, reached = new Set(); api.state.manifest = manifest;
  for (const entry of manifest.episodes) {
    await api.loadEpisode(entry.id);
    const episode = read(`agent/${entry.file}`);
    for (let seat = 0; seat < 4; seat++) {
      control('player').value = `ai_${seat}`; api.selectPlayer();
      assert.equal(storeValue.me.playerId, `ai_${seat}`);
      assert(api.state.steps.length > 0);
      if (seat === 0 || seat === 3) assert.equal(api.state.steps.at(-1).before.round, 1);
      for (const round of new Set(api.state.steps.map(s => s.before.round))) {
        if (!round) continue;
        api.showStep(api.state.steps.findIndex(s => s.before.round === round)); await api.showBattle();
        const rec = api.state.recording; reached.add(rec.name);
        assert.equal(storeValue.match.field.fieldId, rec.field.fieldId);
        for (const frame of rec.frames) api.pushFrame(frame);
        api.seekBattle(rec.frames.length - 1); raf.pop()();
        assert.equal(emitted.at(-1).msg.gt, rec.frames.at(-1).snap.t);
        assert.equal(emitted.at(-1).msg.fieldId, rec.field.fieldId);
      }
    }
    control('player').value = 'ai_1'; api.selectPlayer();
    api.showStep(api.state.steps.length - 1); await api.showBattle();
    assert.equal(api.state.recording.round, 14);
    api.state.playing = true; api.state.frameIndex = api.state.recording.frames.length;
    await api.tick(performance.now());
    assert.equal(storeValue.match.result.victory, true);
    assert.equal(storeValue.match.result.players[0].alive, false);
    const jsonl = gunzipSync(files.get(`agent/downloads/${entry.id}.jsonl.gz`)).toString().trim().split('\n').map(JSON.parse);
    assert.equal(jsonl.at(-1).type, 'terminal');
    assert.equal(jsonl.filter(r => r.type === 'transition').length, episode.meta.methodCalls);
  }
  assert.equal(reached.size, recs.length);
  assert(emitted.some(e => e.type === 'b.ev' && e.msg.ev.length));
  assert(requested.some(url => url.startsWith('/dev/recordings/')));
});

test('CLI refuses existing paths and bad hashes before simulation', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'coop-replay-contract-'));
  const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
  const run = args => spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', timeout: 3000 });
  const existing = run(['--trace', 'missing', '--out', dir]);
  assert.equal(existing.status, 1); assert.match(existing.stderr, /already exists/);
  fs.symlinkSync(path.join(dir, 'missing'), path.join(dir, 'link'));
  const symlink = run(['--trace', 'missing', '--out', path.join(dir, 'link')]);
  assert.equal(symlink.status, 1); assert.match(symlink.stderr, /already exists/);
  const trace = path.join(dir, 'trace.json'); fs.writeFileSync(trace, '{}');
  const bad = run(['--trace', trace, '--out', path.join(dir, 'new')]);
  assert.equal(bad.status, 1); assert.match(bad.stderr, /SHA256 mismatch/);
  assert(!fs.existsSync(path.join(dir, 'new')));
  assert.equal(run(['--trace', trace, '--out', path.join(dir, 'new'), '--rss-mb', '100']).status, 1);
});
