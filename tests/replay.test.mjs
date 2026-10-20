import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import net from 'node:net';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import { Recorder, viewerIds } from '../arknights/replay/recorder.mjs';
import { RULES, SCHEMA, battleViews, bundle, mergeIndexes, writeBundle } from '../arknights/replay/export.mjs';
import { ROOT, parseArgs, createPreviewServer } from '../arknights/replay/server.mjs';

async function staticPreview(t) {
  const root = temporary(t);
  const upstream = http.createServer((_req, res) => { res.writeHead(404); res.end(); });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  const proxy = createPreviewServer({ root, upstream: { port: upstream.address().port } });
  proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening');
  t.after(async () => {
    proxy.destroyConnections(); proxy.closeAllConnections();
    await new Promise(resolve => proxy.close(resolve));
    upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve));
  });
  return { root, url: `http://127.0.0.1:${proxy.address().port}` };
}

function rawGet(url, headers = {}, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { headers, method }, res => {
      const chunks = []; res.on('data', b => chunks.push(b));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, bytes: Buffer.concat(chunks) }));
      res.on('error', reject);
    });
    req.on('error', reject); req.end();
  });
}

test('replay controls CSS is served from code without generated replay artifacts', async t => {
  const { url } = await staticPreview(t);
  const response = await fetch(url + '/css/replay.css');
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /text\/css/);
  const css = await response.text();
  assert.match(css, /agent-replay-dock/);
  assert.match(css, /position:\s*fixed/);
});

test('large replay files negotiate lossless gzip and revalidate instead of downloading again', async t => {
  const { root, url } = await staticPreview(t);
  const file = path.join(root, 'results/replays/public/dev/agent/episodes/network-case.json');
  const bytes = Buffer.from(JSON.stringify({ steps: Array.from({ length: 1200 }, (_, seq) => ({
    seq, action: { t: 'g.buy', slot: seq % 4 }, observation: { round: 1, phase: 'PREP',
      own: { funds: 12, hand: [null, null], name: '部署与装备' } },
  })) }));
  fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, bytes);
  const endpoint = url + '/dev/agent/episodes/network-case.json';
  const compressed = await rawGet(endpoint, { 'Accept-Encoding': 'br, gzip' });
  assert.equal(compressed.status, 200);
  assert.equal(compressed.headers['content-encoding'], 'gzip');
  assert.match(compressed.headers.vary, /Accept-Encoding/i);
  assert(compressed.bytes.length < bytes.length / 4, 'Repeated observations should not cross the tunnel in full');
  assert.deepEqual(gunzipSync(compressed.bytes), bytes);
  assert.ok(compressed.headers.etag);
  const cached = await rawGet(endpoint, { 'Accept-Encoding': 'gzip', 'If-None-Match': compressed.headers.etag });
  assert.equal(cached.status, 304); assert.equal(cached.bytes.length, 0);
  const head = await rawGet(endpoint, { 'Accept-Encoding': 'gzip' }, 'HEAD');
  assert.equal(head.headers['content-encoding'], 'gzip'); assert.equal(head.bytes.length, 0);
  const plain = await rawGet(endpoint, { 'Accept-Encoding': 'gzip;q=0, *;q=1' });
  assert.equal(plain.headers['content-encoding'], undefined); assert.deepEqual(plain.bytes, bytes);
  fs.appendFileSync(file, '\n');
  const changed = await rawGet(endpoint, { 'If-None-Match': compressed.headers.etag });
  assert.equal(changed.status, 200); assert.notEqual(changed.headers.etag, compressed.headers.etag);
});

function temporary(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'arknights-replay-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true })); return dir;
}
class FakeBattle {
  constructor(opts) {
    Object.assign(this, { opts, fieldId: opts.fieldId, recordEvents: opts.recordEvents !== false,
      dt: 1 / 30, time: 0, tickCount: 0, finished: false, errorCount: 0 });
    this._ev(['spawn', { id: 7, playerId: opts.players[0].playerId }]);
  }
  _ev() {}
  start() { this._ev(['start']); return 'started'; }
  step() { this.time = ++this.tickCount / 30; this._ev(['tick', this.tickCount]); this.finished = this.tickCount === 4; return 17; }
  forceEnd(reason) { this.finished = true; this.reason = reason; this._ev(['end', reason]); }
  fieldMeta() { return { fieldId: this.fieldId, rect: { rows: 10, cols: 10 }, units: [] }; }
  snapshot() { return { fieldId: this.fieldId, t: this.time, units: [[7]], dps: { partner1: 5 } }; }
  result() { return { errors: this.errorCount, reason: this.reason ?? 'cleared', perPlayer: { learner: { kills: 1 } } }; }
}
function fakeMatch(count = 4) {
  const m = { phase: 'BAND_DRAFT', round: 0, mode: count === 1 ? 'solo' : 'coop', seed: 123,
    difficulty: 'ABYSS', stageId: 'stage', modeId: 'mode', players: new Map(),
    newBattle(opts) { return new this.BattleClass(opts); },
    _applyBand(ps, bandId) { ps.bandId = bandId; },
    pickBand(ps, bandId) { this._applyBand(ps, bandId); return { ok: true }; },
    _applyCard(ps, idx) { ps.choice = idx; },
    pickCard(ps, idx) { this._applyCard(ps, idx); return { ok: true }; },
    pickPersonalChoice(ps, idx) { ps.choice = idx; return { ok: true }; },
    _handle(ps, msg) { if (msg.t === 'g.infoReady') ps.infoReady = true; return { ok: true }; },
    publicView() { return { phase: this.phase, round: this.round,
      players: [...this.players.values()].map(ps => ({ playerId: ps.playerId, seat: ps.seat, alive: true })) }; },
  };
  for (let seat = 0; seat < count; seat++) {
    const ps = { playerId: seat ? `partner${seat}` : 'learner', seat, funds: 4,
      privateView() { return { playerId: this.playerId, funds: this.funds, lp: 3, privateOffer: `secret-${seat}` }; },
      buy(slot) { this.funds--; return { ok: true, slot }; },
      sell() { return { error: 'BLOCKED' }; },
      resolveTemp() { this.cleaned = true; },
      setReady(ready) { this.ready = ready; this.resolveTemp(); return { ok: true }; },
    };
    for (const method of ['refresh', 'freeze', 'levelUp', 'move', 'equip', 'useArt', 'destroy', 'pickReward',
      'setLoadout', 'setNotOwned', 'setDiy']) ps[method] = (...args) => ({ ok: true, args });
    m.players.set(ps.playerId, ps);
  }
  return m;
}
const state = (m, victory = false) => ({ done: true, rules: RULES, seed: m.seed, mode: m.mode,
  victory, rounds: m.round, summary: { victory, reason: victory ? 'actual-win' : 'actual-loss',
    players: [...m.players.keys()].map(playerId => ({ playerId })) } });
function runBattle(m, fieldId, players = [...m.players.values()]) {
  const b = m.newBattle({ fieldId, players, kind: 'normal' });
  assert.equal(b.start(), 'started');
  for (let i = 0; i < 4; i++) assert.equal(b.step(), 17);
  return b;
}
function fixture(t, count = 4) {
  const dir = temporary(t), recordDir = path.join(dir, 'bundle'), m = fakeMatch(count);
  const recorder = new Recorder({ id: `evaluation-s123-${count}`, recordDir }); recorder.attach(m, FakeBattle);
  for (const ps of m.players.values()) m._applyBand(ps, 'a-band');
  m.phase = 'PREP'; m.round = 1;
  for (const ps of m.players.values()) { ps.buy(1); ps.setReady(true); runBattle(m, `n:${ps.playerId}`, [ps]); }
  m.phase = 'RESULT';
  return { m, recorder, recordDir, dir };
}

test('generic native bundles support solo/co-op, actual results, offline views and verifiable artifacts', t => {
  for (const count of [1, 4]) {
    const { m, recorder, recordDir } = fixture(t, count);
    const report = recorder.finish(state(m));
    assert.equal(report.recordDir, recordDir); assert.equal(report.recordings, count);
    const verification = JSON.parse(fs.readFileSync(report.verification));
    const manifest = JSON.parse(fs.readFileSync(path.join(recordDir, 'agent/index.json')));
    const refs = JSON.parse(fs.readFileSync(path.join(recordDir, 'recordings/index.json')));
    assert.equal(manifest.schema, SCHEMA); assert.equal(manifest.episodes.length, count);
    assert.equal(verification.artifactSHA, report.artifactSHA);
    for (const row of verification.files) {
      const bytes = fs.readFileSync(path.join(recordDir, row.file));
      assert.equal(row.bytes, bytes.length);
      assert.equal(row.sha256, createHash('sha256').update(bytes).digest('hex'));
    }
    const ep = JSON.parse(fs.readFileSync(path.join(recordDir, 'agent', manifest.episodes[0].file)));
    assert.equal(ep.meta.players, count); assert.equal(ep.meta.victory, false);
    assert.equal(ep.meta.summary.reason, 'actual-loss');
    for (let seat = 0; seat < count; seat++) {
      const own = ep.steps.filter(e => e.playerId === `ai_${seat}`);
      assert.equal(own.filter(e => e.terminated).length, 1);
      assert.equal(own.find(e => e.terminated).observation.phase, 'RESULT');
      assert(own.every(e => e.reward === null));
      assert(own.every(e => e.observation.own.privateOffer === `secret-${seat}`));
      if (seat) assert(own.every(e => e.source !== 'policy'));
    }
    assert.equal(refs.length, count);
    const rec = JSON.parse(fs.readFileSync(path.join(recordDir, 'recordings', refs[0].file)));
    assert.equal(rec.version, 1); assert.equal(rec.tick, 1 / 30);
    assert.deepEqual(rec.frames.map(f => f.snap.t), [0, 0.1, 4 / 30]);
    assert.equal(rec.frames.flatMap(f => f.ev).length, 6);
    assert.equal(rec.result.reason, 'cleared');
    assert(!recorder.battles.some(b => b instanceof FakeBattle));
    assert.deepEqual(recorder.finish(state(m)), report);
  }
});

test('wrappers preserve actual calls/results and capture partners, all operations and nested cleanup', t => {
  const m = fakeMatch(), recorder = new Recorder({ id: 'methods', recordDir: path.join(temporary(t), 'bundle') });
  recorder.attach(m, FakeBattle);
  const learner = m.players.get('learner'), partner = m.players.get('partner1');
  const original = learner.buy; learner.buy = (...args) => original(...args);
  assert.deepEqual(learner.buy(2), { ok: true, slot: 2 });
  assert.equal(recorder.events.length, 1);
  assert.equal(recorder.events[0].observation.own.funds, 4);
  assert.equal(recorder.events[0].next_observation.own.funds, 3);
  recorder.source = 'policy'; partner.buy(1);
  assert.equal(recorder.events.at(-1).source, 'partner-controller');
  learner.setReady(true);
  assert.equal(recorder.events.at(-1).source, 'engine');
  assert.equal(recorder.events.at(-1).parentSeq, recorder.events.at(-2).seq);
  for (const method of ['refresh', 'freeze', 'levelUp', 'sell', 'move', 'equip', 'useArt', 'destroy', 'pickReward',
    'setLoadout', 'setNotOwned', 'setDiy']) learner[method](1);
  assert.equal(recorder.events.find(e => e.method === 'sell').accepted, false);
  m._handle(learner, { t: 'g.infoReady' });
  assert.equal(recorder.events.at(-1).action.t, 'g.infoReady');
  assert.equal(learner.infoReady, true);
  assert(recorder.events.every(e => e.observation && e.next_observation));
});

test('draft attribution distinguishes automatic drafts from externally selected policy drafts', t => {
  const m = fakeMatch(), recorder = new Recorder({ id: 'drafts', recordDir: path.join(temporary(t), 'bundle') });
  recorder.attach(m, FakeBattle);
  m._applyBand(m.players.get('learner'), 'automatic');
  assert.equal(recorder.events[0].source, 'fixed-draft-controller');
  recorder.recordCommand({ cmd: 'reset' }, null, { phase: 'BAND_DRAFT' }, { ok: true });
  m.pickCard(m.players.get('learner'), 2); m._applyCard(m.players.get('partner1'), 1);
  recorder.recordCommand({ cmd: 'step', action: { t: 'g.choice', idx: 2 } }, { round: 1 }, { round: 1 }, { ok: true });
  assert(recorder.events.filter(e => e.playerId === 'learner' && e.action.t === 'g.choice').every(e => e.source === 'policy'));
  assert.equal(recorder.events.at(-1).source, 'partner-controller');
  const command = recorder.commands.at(-1);
  assert.deepEqual(command.before, { round: 1 }); assert.deepEqual(command.after, { round: 1 });
  assert(!JSON.stringify(command).includes('privateOffer'));
});

test('previews/rehearsals are excluded including zero-spawn preview fields', t => {
  const m = fakeMatch(1), recorder = new Recorder({ id: 'preview', recordDir: path.join(temporary(t), 'bundle') });
  recorder.attach(m, FakeBattle);
  const ps = [...m.players.values()];
  for (const [fieldId, options] of [['n:learner', { preview: true }], ['r:learner', undefined]]) {
    const b = m.newBattle({ fieldId, players: ps }, options); b.start(); b.step(); assert.equal(b.pending.length, 0);
  }
  assert.equal(recorder.battles.length, 0);
  runBattle(m, 'n:learner'); assert.equal(recorder.battles.length, 1);
});

test('capture budget is hard and latched; terminal-only seats are supported without inferred rewards', t => {
  const m = fakeMatch(), recordDir = path.join(temporary(t), 'bundle');
  const recorder = new Recorder({ id: 'limited', recordDir, maxBytes: 64 }); recorder.attach(m, FakeBattle);
  assert.throws(() => m.players.get('learner').buy(1), /budget/);
  assert.equal(m.players.get('learner').funds, 4);
  recorder.maxBytes = 999999; assert.throws(() => recorder.check(), /budget/);
  assert(!fs.existsSync(recordDir));
  const other = fakeMatch(), r = new Recorder({ id: 'terminal-only', recordDir }); r.attach(other, FakeBattle);
  other.phase = 'RESULT'; r.finish(state(other, true));
  assert.equal(r.events.length, 4);
  assert(r.events.every(e => e.type === 'terminal' && e.terminated && e.reward === null && e.result === null));
});

test('nonterminal and errored/incomplete battles are never exported', t => {
  const { m, recorder, recordDir } = fixture(t, 1);
  m.phase = 'PREP'; assert.throws(() => recorder.finish(state(m)), /terminal/);
  m.phase = 'RESULT'; recorder.battles[0].result.errors = 1;
  assert.throws(() => recorder.finish(state(m)), /errored/); assert(!fs.existsSync(recordDir));
});

test('finish accepts the sanitized backend terminal/outcome contract without inventing labels', t => {
  const { m, recorder, recordDir } = fixture(t, 1);
  recorder.finish({ terminal: true, rules: RULES, phase: 'RESULT', round: m.round,
    outcome: { summary: { victory: false, reason: 'left', roundsPassed: 0, errors: 0 } } });
  const manifest = JSON.parse(fs.readFileSync(path.join(recordDir, 'agent/index.json')));
  assert.equal(manifest.episodes[0].victory, false);
  assert.equal(manifest.episodes[0].terminalReason, 'left');
  assert.equal(manifest.episodes[0].roundsPassed, 0);
});

test('all repeated fields are reachable as the first native battle in their round', () => {
  const refs = [{ name: 'a', round: 1, fieldId: 'n:ai_0' }, { name: 'b', round: 1, fieldId: 'n:ai_1' },
    { name: 'c', round: 1, fieldId: 'n:ai_1' }, { name: 'd', round: 2, fieldId: 'boss' }];
  const covered = new Set();
  for (const view of battleViews(refs)) for (const round of [1, 2]) covered.add(view.refs.find(r => r.round === round).name);
  assert.deepEqual(covered, new Set(['a', 'b', 'c', 'd']));
  assert.deepEqual(viewerIds({ fieldId: 'n:partner3', dps: { learner: 1, partner1: 2, 'partner-2': 3 } }),
    { fieldId: 'n:ai_3', dps: { ai_0: 1, ai_1: 2, ai_2: 3 } });
});

test('new directories, safe files and collision checks preserve existing artifacts', t => {
  const { m, recorder, recordDir } = fixture(t, 1); recorder.finish(state(m));
  const options = { recordDir, id: recorder.id, state: state(m), recorder };
  const before = fs.readFileSync(path.join(recordDir, 'agent/index.json'));
  assert.throws(() => writeBundle(options), /must be new/);
  assert.deepEqual(fs.readFileSync(path.join(recordDir, 'agent/index.json')), before);
  assert.throws(() => new Recorder({ id: '../escape', recordDir }), /Unsafe/);
  assert.throws(() => bundle({ ...options, maxBytes: 1 }), /budget/);
  const files = bundle(options), manifest = JSON.parse(files.get('agent/index.json')), refs = JSON.parse(files.get('recordings/index.json'));
  assert.throws(() => mergeIndexes(manifest, refs, manifest, refs), /collision/);
  assert.throws(() => mergeIndexes({ schema: SCHEMA, episodes: [] }, [], manifest, []), /Missing recording/);
  const bad = structuredClone(manifest); bad.episodes[0].file = '../escape';
  assert.throws(() => mergeIndexes({ schema: SCHEMA, episodes: [] }, [], bad, refs), /Unsafe/);
  const missingParent = path.join(temporary(t), 'missing', 'bundle');
  assert.throws(() => writeBundle({ ...options, recordDir: missingParent }), /ENOENT/);
});

test('server options and project root are independent of legacy layout config', () => {
  assert.equal(ROOT, fileURLToPath(new URL('../', import.meta.url)).replace(/\/$/, ''));
  assert.deepEqual(parseArgs([]), { port: 38181, host: '0.0.0.0' });
  assert.equal(parseArgs(['--port', '38182', '--replay-dir', '.']).port, 38182);
  assert.equal(parseArgs(['--evaluations', '--port', '38182']).evaluations, true);
  for (const args of [['--port'], ['--port', '0'], ['--port', 'bad'], ['--unknown', 'a']]) assert.throws(() => parseArgs(args));
});

test('private mount overrides indexes, preserves live files and identifies /healthz; HTTP/WS close cleanly', async t => {
  const root = temporary(t), replayDir = path.join(root, 'private'), webDir = path.join(root, 'web');
  const write = (file, data) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, data); };
  const liveFile = path.join(root, 'results/replays/public/dev/agent/index.json');
  write(liveFile, 'existing-45'); write(path.join(replayDir, 'agent/index.json'), 'private-case');
  write(path.join(webDir, 'js/main.js'), 'exact-overlay');
  const upstream = http.createServer((_req, res) => res.end('upstream'));
  const upgradeSockets = new Set();
  upstream.on('upgrade', (_req, socket) => {
    upgradeSockets.add(socket); socket.on('close', () => upgradeSockets.delete(socket));
    socket.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: test\r\n\r\n');
  });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  const proxy = createPreviewServer({ upstream: { port: upstream.address().port }, root, replayDir, webDir });
  proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening');
  t.after(async () => {
    proxy.destroyConnections(); proxy.closeAllConnections();
    await new Promise(resolve => proxy.close(resolve));
    for (const socket of upgradeSockets) socket.destroy();
    upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve));
  });
  const url = `http://127.0.0.1:${proxy.address().port}`;
  const health = await (await fetch(url + '/healthz')).json();
  assert.equal(health.arknightsPreview, 1); assert.equal(health.rules, RULES); assert.equal(health.root, root);
  assert.equal(health.collect.supported, false);
  assert.equal(health.evaluations, false);
  const retired = await fetch(url + '/collect?id=old-invite', { redirect: 'manual' });
  assert.equal(retired.status, 302); assert.equal(retired.headers.get('location'), '/');
  assert.equal(await (await fetch(url + '/dev/agent/index.json')).text(), 'private-case');
  assert.equal(fs.readFileSync(liveFile, 'utf8'), 'existing-45');
  assert.equal(await (await fetch(url + '/js/main.js')).text(), 'exact-overlay');
  assert.equal(await (await fetch(url + '/elsewhere')).text(), 'upstream');
  assert.equal((await fetch(url + '/dev/%2eprivate')).status, 403);
  const head = await fetch(url + '/js/main.js', { method: 'HEAD' }); assert.equal(await head.text(), '');
  const socket = net.connect(proxy.address().port, '127.0.0.1'); await once(socket, 'connect');
  socket.write('GET /socket HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: test\r\n\r\n');
  assert.match((await once(socket, 'data'))[0].toString(), /101 Switching/);
  const closed = once(socket, 'close'); proxy.destroyConnections(); await closed;
});
