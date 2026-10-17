import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';
import { ShopEnv, RULES } from '../rl/env.mjs';
import { Recorder, viewerIds } from './record.mjs';
import { mergeIndexes } from './index.mjs';

export const CONTROLLER = 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1';
const SCHEMA = 'arknights.bot-transitions.v1';
const MAX_BYTES = 20_000_000;
const resultKeys = ['seed', 'mode', 'difficulty', 'budget', 'victory', 'rounds', 'steps',
  'engineErrors', 'actionErrors', 'controllerErrors', 'budgetForcedReady', 'controllerBlockedSales'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
export async function fileHash(filename) {
  const h = createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) h.update(chunk);
  return h.digest('hex');
}
export function validateTrace(trace) {
  assert.equal(trace.rules, RULES, 'Trace rules mismatch');
  assert.equal(trace.controller, CONTROLLER, 'Trace controller mismatch');
  const r = trace.result;
  assert(r && typeof r === 'object', 'Missing terminal result');
  assert.deepEqual(Object.keys(r).sort(), [...resultKeys].sort(), 'Unexpected terminal result fields');
  assert.equal(r.mode, 'solo');
  assert(['NORMAL', 'HARD', 'ABYSS'].includes(r.difficulty), 'Invalid difficulty');
  assert(Number.isInteger(r.seed) && r.seed >= 1 && r.seed <= 0xffffffff, 'Invalid seed');
  assert(Number.isInteger(trace.budget) && trace.budget >= 1 && trace.budget <= 1024, 'Invalid budget');
  assert.equal(r.budget, trace.budget, 'Trace budget mismatch');
  assert.equal(typeof r.victory, 'boolean');
  for (const k of resultKeys.slice(5)) assert(Number.isInteger(r[k]) && r[k] >= 0, `Invalid result ${k}`);
  assert(Array.isArray(trace.actions) && trace.actions.length > 0 && trace.actions.length <= 100_000,
    'Empty or oversized actions');
  assert.equal(r.steps, trace.actions.length, 'Trace steps mismatch');
  for (const [i, a] of trace.actions.entries()) {
    assert(Number.isInteger(a.round) && a.round >= 1, `Invalid round at action ${i}`);
    assert(Number.isInteger(a.candidateIndex) && a.candidateIndex >= 0, `Invalid candidateIndex at action ${i}`);
    assert(['buy', 'sell', 'refresh', 'levelUp', 'ready'].includes(a.action?.type), `Invalid action ${i}`);
  }
  return trace;
}
export function verifyState(state, trace) {
  assert.equal(state.rules, trace.rules, 'Environment rules drift');
  assert.equal(state.controller, trace.controller, 'Environment controller drift');
  const { seed, mode, difficulty, budget } = trace.result;
  assert.deepEqual(state.episode, { seed, mode, difficulty, budget }, 'Environment configuration drift');
  for (const k of ['engineErrors', 'actionErrors', 'controllerErrors']) assert.equal(state[k], 0, k);
}
export function verifyCandidate(state, entry, index) {
  assert.equal(state.done, false, `Trace continues past terminal at ${index}`);
  assert.equal(state.rounds, entry.round, `Round mismatch at action ${index}`);
  assert(entry.candidateIndex < state.candidates.length, `Candidate out of range at action ${index}`);
  // Entire candidate binding, not just action type: UID, slot, id, price, tier, bonds, golden.
  assert.deepEqual(state.candidates[entry.candidateIndex], entry.action, `Candidate binding mismatch at action ${index}`);
}
export function verifyTerminal(state, trace) {
  assert.equal(state.done, true, 'Trace ended before terminal');
  const actual = { ...state.episode };
  for (const k of resultKeys.slice(4)) actual[k] = state[k];
  assert.deepEqual(actual, trace.result, 'Terminal result mismatch');
  assert.equal(state.summary.victory, state.victory);
  assert.equal(state.summary.roundsPassed, state.rounds);
  assert.equal(state.reward, state.victory ? 1 : -1);
}

// Validate the actual fields read by agentReplay.js (selectPlayer/showStep/enterRecording/
// pushFrame/seekBattle), plus ownership and complete battle/event capture.
export function validateViewer(episode, recordings) {
  const steps = episode.steps.filter(s => s.playerId === 'ai_0');
  assert.equal(steps.length, episode.steps.length);
  assert(steps.length > 0 && steps.some(s => s.before.round >= 1));
  assert(steps.some(s => s.action.t === 'g.band'), 'Reset draft was not captured');
  const terminal = steps.filter(s => s.terminated);
  assert.equal(terminal.length, 1);
  assert.equal(terminal[0].observation.phase, 'RESULT');
  assert.equal(terminal[0].reward, episode.meta.victory ? 1 : -1);
  for (const s of steps) {
    assert(s.action.t && typeof s.before.round === 'number' && typeof s.accepted === 'boolean');
    assert(s.observation.public.players.some(p => p.playerId === s.playerId));
    assert(s.observation.own && s.methodObservation.own && s.beforeObservation.own);
  }
  assert(recordings.length > 0, 'No real battles recorded');
  assert.equal(episode.meta.battles.length, recordings.length);
  for (const rec of recordings) {
    assert.equal(rec.version, 1);
    assert.equal(rec.tick, 1 / 30);
    assert(rec.field.fieldId && rec.field.rect && Array.isArray(rec.field.units));
    assert(rec.frames.length > 0);
    assert(steps.some(s => s.before.round === rec.round && s.action.t === 'g.ready' && s.accepted),
      `No ready action for battle round ${rec.round}`);
    assert.equal(rec.frames[0].snap.t, 0, 'Missing initial battle frame');
    let previous = -1;
    const units = new Set(rec.field.units.map(u => u.id));
    for (const f of rec.frames) {
      assert.equal(f.snap.fieldId, rec.field.fieldId);
      assert(f.snap.t > previous && Array.isArray(f.snap.units) && Array.isArray(f.ev));
      previous = f.snap.t;
      for (const ev of f.ev) {
        assert(Array.isArray(ev) && typeof ev[0] === 'string');
        if (ev[0] === 'spawn') units.add(ev[1].id);
      }
      for (const u of f.snap.units) assert(units.has(u[0]), `Missing spawn metadata for unit ${u[0]}`);
    }
    assert(Math.abs(previous - rec.duration) <= 0.001, 'Missing final battle frame');
    assert.equal(rec.result.errors, 0);
  }
}

export async function replay({ trace: traceFile, checkpoint, out, game,
  checkpointSha256: expectedCheckpointHash }) {
  const started = performance.now();
  let peakRss = 0;
  const guard = () => {
    peakRss = Math.max(peakRss, process.memoryUsage.rss(), process.resourceUsage().maxRSS * 1024);
    assert(peakRss <= 768 * 1024 * 1024, 'RSS exceeds 768 MiB');
    assert(performance.now() - started < 25_000, 'Replay exceeds 25 seconds');
  };
  assert(traceFile && checkpoint && out, 'Required: --trace --checkpoint --out');
  const output = path.resolve(out);
  const parent = fs.realpathSync(path.dirname(output));
  const temp = fs.realpathSync(os.tmpdir());
  assert(parent === temp || parent.startsWith(`${temp}${path.sep}`), '--out must be a new directory under the system temp directory');
  assert(!fs.existsSync(output), 'Output directory already exists');
  assert(fs.statSync(traceFile).size <= MAX_BYTES, 'Trace exceeds 20 MB');
  const traceBytes = fs.readFileSync(traceFile), trace = validateTrace(JSON.parse(traceBytes));
  const traceHash = hash(traceBytes);
  const checkpointHash = await fileHash(checkpoint);
  if (expectedCheckpointHash) assert.equal(checkpointHash, expectedCheckpointHash, 'Checkpoint hash mismatch');
  const id = `model-${trace.result.mode}-${trace.result.difficulty}-s${trace.result.seed}-${traceHash.slice(0, 12)}-${checkpointHash.slice(0, 12)}`;
  const gameRoot = path.resolve(game || fileURLToPath(new URL('../../Stronghold-Protocol', import.meta.url)));
  const base = await ShopEnv.create(gameRoot); // Enforces pinned rules and clean rule sources.
  const { Battle } = await import(pathToFileURL(path.join(gameRoot, 'server/sim/Battle.js')));
  const recorder = new Recorder({ id, guard });
  const OriginalMatch = base.Match;
  class ObservedMatch extends OriginalMatch {
    constructor(opts) { super(opts); recorder.attach(this, Battle); }
  }
  const env = new ShopEnv({ Match: ObservedMatch, VirtualScheduler: base.VirtualScheduler,
    data: base.data, bot: base.bot });
  recorder.env = env;
  let state, recordings, simulationMs;
  try {
    const simulationStart = performance.now();
    state = env.reset(trace.result);
    verifyState(state, trace);
    for (const [i, entry] of trace.actions.entries()) {
      guard();
      verifyCandidate(state, entry, i);
      const candidateCount = state.candidates.length;
      recorder.traceIndex = i;
      const first = recorder.events.length;
      state = env.step(entry.candidateIndex);
      recorder.traceIndex = null;
      verifyState(state, trace);
      const calls = recorder.events.slice(first).filter(e => e.source === 'policy' && e.parentSeq === null &&
        ['buy', 'sell', 'refresh', 'levelUp', 'setReady'].includes(e.method));
      assert.equal(calls.length, 1, `Policy call count at ${i}`);
      assert.equal(calls[0].method, entry.action.type === 'ready' ? 'setReady' : entry.action.type);
      assert.equal(calls[0].accepted, true);
      calls[0].policy = { traceIndex: i, candidateIndex: entry.candidateIndex, candidate: entry.action,
        candidateCount };
    }
    verifyTerminal(state, trace);
    recordings = recorder.finish(env);
    simulationMs = performance.now() - simulationStart;
    for (const row of recorder.events) {
      if (['buy', 'sell', 'refresh', 'levelUp'].includes(row.method) && row.accepted)
        assert.equal(row.source, 'policy', 'Controller performed shop economy');
    }
    assert.equal(recorder.events.filter(e => e.result?.error === 'RL_CONTROLLER_NO_SALES').length,
      state.controllerBlockedSales, 'Missing blocked controller sale calls');
    assert.equal(recorder.events.filter(e => e.method === 'setReady' && e.source === 'budget-controller').length,
      state.budgetForcedReady, 'Missing forced ready calls');
  } finally { env.close(); base.close(); }
  guard();
  const sources = {};
  for (const name of ['env.mjs', 'controller.mjs', 'features.mjs', 'learner.py'])
    sources[`rl/${name}`] = await fileHash(fileURLToPath(new URL(`../rl/${name}`, import.meta.url)));
  const provenance = { trace: { path: path.resolve(traceFile), sha256: traceHash },
    checkpoint: { path: path.resolve(checkpoint), sha256: checkpointHash,
      binding: 'caller-supplied; learner eval trace contains no checkpoint identity; not independently attested' },
    rules: RULES, controller: trace.controller, budget: trace.budget, sources,
    node: process.version, playerIdMap: { learner: 'ai_0' },
    verification: { selectedCandidates: trace.actions.length, terminalFields: resultKeys,
      completeTerminal: true, checkpointInferenceRerun: false, simulationMs } };
  const refs = recordings.map(r => ({ name: r.name, title: r.title, file: `${r.name}.json`,
    kind: r.kind, stageId: r.stageId, round: r.round, duration: r.duration }));
  const counts = {};
  for (const e of recorder.events) counts[e.action.t] = (counts[e.action.t] || 0) + 1;
  const decisions = recorder.events.filter(e => e.source !== 'engine');
  const meta = { id, config: `solo:${state.episode.difficulty}`, seed: state.episode.seed, players: 1,
    commit: RULES, source: 'learner-eval-trace+fixed-controller', rehearsal: 0, schema: SCHEMA,
    observation_scope: 'own-private-plus-public-view', provenance,
    note: state.victory ? '学习模型真实终局重放；checkpoint 绑定见 provenance。'
      : '失败重放，不是通关录像；checkpoint 绑定见 provenance。',
    victory: state.victory, roundsPassed: state.rounds, hiddenReached: state.summary.hiddenReached,
    policySteps: state.steps, steps: decisions.length, methodCalls: recorder.events.length,
    accepted: decisions.filter(e => e.accepted).length,
    engineErrors: state.engineErrors, schedulerErrors: 0, wallMs: performance.now() - started,
    actions: counts, battles: refs, terminalReason: state.summary.reason, summary: state.summary };
  const episode = viewerIds({ meta, engineCalls: recorder.events.filter(e => e.source === 'engine'),
    steps: decisions.map(e => ({ seq: e.seq, playerId: e.playerId,
    action: e.action, accepted: e.accepted, result: e.result, reward: e.reward, terminated: e.terminated,
    source: e.source, method: e.method, parentSeq: e.parentSeq, policy: e.policy,
    observation: e.terminal_observation || e.next_observation, methodObservation: e.next_observation,
    beforeObservation: e.observation, before: { phase: e.observation.phase, round: e.observation.round,
      funds: e.observation.own.funds, lp: e.observation.own.lp } })) });
  const viewerRecordings = viewerIds(recordings);
  // Keep engine-to-viewer mapping literal in provenance instead of mapping its own explanation.
  episode.meta.provenance = provenance;
  validateViewer(episode, viewerRecordings);
  const raw = [{ type: 'episode', ...meta }, ...recorder.events, { type: 'terminal', summary: state.summary }]
    .map(row => JSON.stringify(row)).join('\n') + '\n';
  const compressed = gzipSync(raw);
  const transitionHash = hash(recorder.events.map(e => JSON.stringify(e)).join('\n'));
  const { summary: _summary, ...indexMeta } = episode.meta;
  const manifest = { schema: SCHEMA, createdAt: new Date().toISOString(), commit: RULES,
    quality: '学习模型实际 eval trace 重放，保留失败；非专家或通关保证。',
    episodes: [{ ...indexMeta, bytes: compressed.length, transitionHash, file: `episodes/${id}.json` }],
    totalSteps: meta.steps, totalBytes: compressed.length };
  mergeIndexes({ schema: SCHEMA, episodes: [] }, [], manifest, refs);
  const files = new Map([
    [`agent/episodes/${id}.json`, Buffer.from(JSON.stringify(episode))],
    [`agent/downloads/${id}.jsonl.gz`, compressed],
    ...viewerRecordings.map(r => [`recordings/${r.name}.json`, Buffer.from(JSON.stringify(r))]),
    ['recordings/index.json', Buffer.from(JSON.stringify(refs))],
    ['agent/index.json', Buffer.from(JSON.stringify(manifest))],
  ]);
  guard();
  const report = { id, victory: state.victory, outcome: state.victory ? 'victory' : 'failure replay, not a winning run',
    result: trace.result, policySteps: state.steps, methodCalls: meta.methodCalls, battles: recordings.length,
    frames: recordings.reduce((n, r) => n + r.frames.length, 0),
    battleEvents: recordings.reduce((n, r) => n + r.frames.reduce((s, f) => s + f.ev.length, 0), 0),
    provenance, limits: { wallMs: 25000, rssBytes: 768 * 1024 * 1024, outputBytes: MAX_BYTES },
    simulationMs, elapsedMs: performance.now() - started, peakRssBytes: peakRss,
    payloadBytes: [...files.values()].reduce((n, b) => n + b.length, 0),
    files: [...files].map(([file, b]) => ({ file, bytes: b.length, sha256: hash(b) })) };
  files.set('verification.json', Buffer.from(JSON.stringify(report, null, 2)));
  const totalBytes = [...files.values()].reduce((n, b) => n + b.length, 0);
  assert(totalBytes <= MAX_BYTES, 'Output exceeds 20 MB');
  guard();
  // Reserve fresh directory atomically only after complete replay/validation. Never overwrite
  // existing paths (including an empty directory/symlink). No writes to the live game's indexes.
  fs.mkdirSync(output);
  for (const d of ['agent', 'agent/episodes', 'agent/downloads', 'recordings']) fs.mkdirSync(path.join(output, d));
  // Publish the agent index last: incomplete artifacts are not an importable episode index.
  const index = files.get('agent/index.json'); files.delete('agent/index.json'); files.set('agent/index.json', index);
  for (const [name, bytes] of files) { guard(); fs.writeFileSync(path.join(output, name), bytes, { flag: 'wx' }); }
  guard();
  return { ...report, out: output, totalBytes, elapsedMs: performance.now() - started, peakRssBytes: peakRss };
}
