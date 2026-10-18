import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import { hash, limits } from '../coop-replay/replay.mjs';
import { configuration, validateTrace, verifyState, verifyTerminal, verifyCandidate, KIND as TRAINING_KIND } from '../coop-replay/verify.mjs';
import { profileFor } from '../coop-replay/profiles.mjs';
import { workerLaunch } from '../coop-replay/cli.mjs';
import { bundle, mergeIndexes } from '../coop-replay/export.mjs';
import { PROFILE, KIND, CONTROLLER, CHECKPOINT, LOADER, LABEL, workerEnvironment, receiptMetadata } from './profile.mjs';
import { fixture } from './fixtures.mjs';

// Static parsing only: neither this trace nor its actions are executed here.
const tracePath = fileURLToPath(new URL('../../experiments/wide-seed65-frozen-trace-v1.json', import.meta.url));
const receiptPath = fileURLToPath(new URL('../../experiments/wide-seed65-reproduction-v1.json', import.meta.url));
const traceBytes = fs.readFileSync(tracePath), receiptBytes = fs.readFileSync(receiptPath);
const trace = JSON.parse(traceBytes), traceSHA256 = hash(traceBytes);
const options = { profile: PROFILE, trace: tracePath, traceSha256: traceSHA256,
  receipt: receiptPath, receiptSha256: hash(receiptBytes) };
const metadata = () => ({ kind: KIND, controller: CONTROLLER, ...receiptMetadata(trace, traceSHA256, options) });

test('real small trace static contracts; defaults stay training and fixed allowlist rejects drift', () => {
  assert.equal(traceBytes.length, 416577);
  assert.equal(traceSHA256, 'd1fdb2b3ad671f0e342cc0229fd52c1500ee0a546e2574463d71da973d3265c7');
  assert.equal(options.receiptSha256, '6b01ecc49b507b3b320118c454b644cc46059a936bcfad70ab3ac80a7d1c9922');
  validateTrace(trace, PROFILE);
  assert.throws(() => validateTrace(trace));
  assert.equal(profileFor().kind, TRAINING_KIND);
  assert.throws(() => profileFor({ kind: KIND, controller: CONTROLLER }));
  assert.throws(() => validateTrace(trace, 'unknown'));
  assert.deepEqual(configuration(trace), { pool: 'normal', split: 'eval', poolSeed: 313,
    fixed: ['original-v1', 'original-v1', 'original-v1'] });
  const { ok, ...state } = structuredClone(trace.terminal);
  verifyState(state, trace, PROFILE); verifyTerminal(state, trace);
  assert.throws(() => verifyState(state, trace));
  for (const mutate of [t => t.kind = TRAINING_KIND, t => t.controller = profileFor().controller,
    t => t.checkpointSHA256 = '0'.repeat(64), t => t.terminal.controller = profileFor().controller,
    t => t.terminal.episode.partners.split = 'train', t => t.terminal.episode.partners.fixed = null,
    t => t.terminal.episode.partners.seats[0].id = 'buy2-v1', t => t.terminal.summary.players[0].victory = true,
    t => t.terminal.summary.players[0].eliminatedRound = 11, t => t.actions.pop(),
    t => t.terminal.summary.players[3].alive = false, t => t.terminal.partnerActionErrors = 1]) {
    const bad = structuredClone(trace); mutate(bad); assert.throws(() => validateTrace(bad, PROFILE));
  }
  for (const mutate of [s => s.summary.durationMs++, s => s.summary.players[0].lp++,
    s => s.partnerAudit['partner-1'].actionSHA256 = '0'.repeat(64), s => s.extra = true,
    s => s.controllerBlockedMoves++, s => s.episode.diagnostics.rejectedActions.push({ bad: true }), s => s.done = false]) {
    const bad = structuredClone(state); mutate(bad); assert.throws(() => verifyTerminal(bad, trace));
  }
  const entry = trace.actions.find(a => a.action.type === 'buy');
  const current = { done: false, rounds: entry.round, candidates: Array.from({ length: entry.candidateIndex + 1 }, () => entry.action) };
  verifyCandidate(current, entry, 0);
  for (const [key, value] of Object.entries({ slot: -1, id: 'wrong', uid: 'wrong', price: -1, tier: -1, golden: !entry.action.golden, bonds: ['wrong'] })) {
    const bad = structuredClone(entry); bad.action[key] = value; assert.throws(() => verifyCandidate(current, bad, 0));
  }
});

test('receipt is hashed, bound and attributed, never a local inference assertion', () => {
  const p = metadata();
  assert.equal(p.singleCheckpoint, CHECKPOINT); assert.equal(p.inferenceRerun, false);
  assert.equal(p.independentPythonReproduction.reportedInferenceRerun, true);
  assert.equal(p.generalizationImprovementEstablished, false);
  assert.throws(() => receiptMetadata(trace, traceSHA256, { ...options, receiptSha256: '0'.repeat(64) }));
  assert.throws(() => receiptMetadata(trace, '0'.repeat(64), options));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'frozen-receipt-'));
  try {
    for (const mutate of [r => r.status = 'failed', r => r.inferenceRerun = false,
      r => r.manifest.sha256 = '0'.repeat(64), r => r.wholeWideRunFinalized = true,
      r => r.comparisons[1].exactPayloads['actions.jsonl'] = false, r => r.games[1].rounds = 13,
      r => r.evidence[r.games[1].id]['terminal.json'].sha256 = '0'.repeat(64),
      r => r.source.prep.implementation['loader.mjs'].sha256 = '0'.repeat(64)]) {
      const bad = JSON.parse(receiptBytes); mutate(bad);
      const bytes = Buffer.from(JSON.stringify(bad)), receipt = path.join(dir, 'receipt.json'); fs.writeFileSync(receipt, bytes);
      assert.throws(() => receiptMetadata(trace, traceSHA256, { ...options, receipt, receiptSha256: hash(bytes) }));
    }
  } finally { fs.rmSync(dir, { recursive: true }); }
});

test('production worker launch enables exact original loader before imports; synthetic inherited-controller checks', () => {
  const clean = workerEnvironment({ PATH: process.env.PATH, ARK_PREP_TIMING: 'off', COOP_TEAM: 'wrong',
    NODE_OPTIONS: '--loader bad', NODE_PATH: '/bad', STRONGHOLD_GAME: '/bad' });
  assert.deepEqual(clean, { PATH: process.env.PATH, ARK_PREP_TIMING: 'deferred' });
  const launch = workerLaunch(options);
  assert.equal(launch.args[3], fileURLToPath(LOADER));
  assert.equal(launch.env.ARK_PREP_TIMING, 'deferred');
  assert(!workerLaunch({}).args.includes('--loader'));
  assert.deepEqual(limits({}), { seconds: 12, rssMb: 768, heapMb: 512, maxOutputMb: 12, frameEvery: 3 });
  for (const timing of ['deferred', 'off']) {
    const result = spawnSync(process.execPath, [...launch.args.slice(0, -2),
      fileURLToPath(new URL('../prep-timing/timing.test.mjs', import.meta.url))],
    { env: { ...launch.env, ARK_PREP_TIMING: timing }, timeout: 4000, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr + result.stdout);
  }
});

test('new CLI rejects missing SHA, wrong kind/hash, record prefix and invalid budgets before any game creation', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'frozen-cli-'));
  const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
  const common = ['--trace', tracePath, '--out', path.join(dir, 'new'), '--receipt', receiptPath,
    '--receipt-sha256', options.receiptSha256];
  const run = args => spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', timeout: 2500 });
  try {
    for (const [args, error] of [
      [common, /explicit --trace-sha256/],
      [[...common, '--trace-sha256', '0'.repeat(64)], /Trace SHA256 mismatch/],
      [[...common, '--trace-sha256', traceSHA256, '--mode', 'record', '--prefix', '1'], /Prefix diagnostics/],
      [[...common, '--trace-sha256', traceSHA256, '--rss-mb', '100'], /Heap budget/],
    ]) {
      const result = run(args); assert.equal(result.status, 1, result.stderr); assert.match(result.stderr, error);
      assert(!fs.existsSync(path.join(dir, 'new')));
    }
    const bad = { ...trace, kind: TRAINING_KIND }, bytes = Buffer.from(JSON.stringify(bad));
    const file = path.join(dir, 'bad.json'); fs.writeFileSync(file, bytes);
    const result = run(['--trace', file, '--trace-sha256', hash(bytes), '--out', path.join(dir, 'new'),
      '--receipt', receiptPath, '--receipt-sha256', options.receiptSha256]);
    assert.equal(result.status, 1); assert.match(result.stderr, /actual-training-trajectory/);
    assert(!fs.existsSync(path.join(dir, 'new')));
  } finally { fs.rmSync(dir, { recursive: true }); }
});

test('64 synthetic fields reachable through unmodified native UI; AI2/3/4 select R14; learner stops R12', async () => {
  const input = fixture(metadata(), trace.terminal), files = bundle(input);
  assert.throws(() => bundle({ ...input, state: { ...input.state, done: false } }), /terminal/);
  assert.throws(() => bundle({ ...input, maxBytes: 10 }), /budget/);
  const read = name => JSON.parse(files.get(name)), manifest = read('agent/index.json'), recs = read('recordings/index.json');
  assert.equal(recs.length, 64); assert.equal(manifest.quality, KIND);
  assert([...files.values()].reduce((n, b) => n + b.length, 0) < 4_000_000);
  assert.equal(mergeIndexes({ schema: manifest.schema, episodes: [] }, [], manifest, recs).recordings.length, 64);
  for (const [name, bytes] of files) {
    const text = name.endsWith('.gz') ? gunzipSync(bytes).toString() : bytes.toString();
    assert(!/PPO|实际训练|合作训练/.test(text), name);
  }
  const elements = new Map(), emitted = [], raf = [];
  const control = name => {
    if (!elements.has(name)) elements.set(name, { value: '', textContent: '',
      replaceChildren(...children) { this.children = children; this.value = children[0]?.value || ''; } });
    return elements.get(name);
  };
  let storeValue = {};
  const context = vm.createContext({ URL, performance, structuredClone, console, t: s => s, emptyMatch: () => ({}),
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    location: { href: 'http://local/?replay=synthetic' }, history: { replaceState() {} }, requestAnimationFrame: fn => raf.push(fn),
    fetch: async url => ({ ok: true, json: async () => read(url.slice(5)) }),
    store: { set(v) { storeValue = v; }, get: () => storeValue, patch(k, v) { storeValue[k] = { ...storeValue[k], ...v }; } },
    net: { _emit: (type, msg) => emitted.push({ type, msg }) },
    testControls: { querySelector: selector => control(selector.match(/"([^"]+)"/)[1]) } });
  const game = process.env.STRONGHOLD_GAME || fileURLToPath(new URL('../../Stronghold-Protocol', import.meta.url));
  const source = fs.readFileSync(path.join(game, 'public/js/agentReplay.js'), 'utf8');
  vm.runInContext(source.replace(/^import .*;\n/gm, '').replace('export async function bootReplay', 'async function bootReplay')
    + '\ncontrols = testControls; globalThis.api = { state, loadEpisode, selectPlayer, showStep, showBattle, seekBattle, pushFrame, tick };', context);
  const api = context.api, reached = new Set(); api.state.manifest = manifest;
  for (const entry of manifest.episodes) {
    assert(entry.policyLabel.startsWith(LABEL)); assert.equal(entry.source, KIND);
    await api.loadEpisode(entry.id);
    for (let seat = 0; seat < 4; seat++) {
      control('player').value = `ai_${seat}`; api.selectPlayer();
      assert.equal(storeValue.me.playerId, `ai_${seat}`);
      assert.equal(api.state.steps.at(-1).before.round, seat ? 14 : 12);
      for (const round of new Set(api.state.steps.map(s => s.before.round))) {
        if (!round) continue;
        api.showStep(api.state.steps.findIndex(s => s.before.round === round)); await api.showBattle();
        const rec = api.state.recording; reached.add(rec.name);
        assert.equal(storeValue.match.field.fieldId, rec.field.fieldId);
        api.seekBattle(rec.frames.length - 1); raf.pop()();
        assert.equal(emitted.at(-1).msg.gt, rec.duration);
      }
      if (seat) {
        api.showStep(api.state.steps.length - 1); await api.showBattle();
        assert.equal(api.state.recording.round, 14);
        api.state.playing = true; api.state.frameIndex = api.state.recording.frames.length;
        await api.tick(performance.now());
        assert.equal(storeValue.match.result.victory, true);
        assert.equal(storeValue.match.result.players[0].victory, false);
        assert.equal(storeValue.match.result.players[0].eliminatedRound, 12);
      }
    }
    const lines = gunzipSync(files.get(`agent/downloads/${entry.id}.jsonl.gz`)).toString().trim().split('\n').map(JSON.parse);
    assert.equal(lines[0].source, KIND); assert.equal(lines[0].provenance.inferenceRerun, false);
    const { type, ...terminal } = lines.at(-1); assert.equal(type, 'terminal');
    assert.deepEqual(terminal, input.state);
    assert.equal(lines.filter(r => r.type === 'transition').length, entry.methodCalls);
  }
  assert.equal(reached.size, 64);
});
