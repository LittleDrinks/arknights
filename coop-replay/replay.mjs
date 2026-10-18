import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { CoopEnv } from '../coop/env.mjs';
import { RULES } from '../rl/env.mjs';
import { Recorder, PLAYER_MAP } from './record.mjs';
import { configuration, validateTrace, verifyState, verifyCandidate, verifyTerminal, TRACE_SHA256 } from './verify.mjs';
import { bundle } from './export.mjs';
import { profileFor } from './profiles.mjs';
import { PROFILE, validateOptions, receiptMetadata } from '../frozen-replay/profile.mjs';

export const hash = bytes => createHash('sha256').update(bytes).digest('hex');
export function limits(options) {
  const number = (key, fallback, max) => {
    const n = Number(options[key] ?? fallback);
    assert(Number.isInteger(n) && n >= 1 && n <= max, `Invalid ${key}: integer 1..${max}`);
    return n;
  };
  return { seconds: number('seconds', 12, 600), rssMb: number('rssMb', 768, 8192),
    heapMb: number('heapMb', 512, 6144), maxOutputMb: number('maxOutputMb', 12, 1024),
    frameEvery: number('frameEvery', 3, 30) };
}

export async function replay(options, progress = () => {}) {
  const started = performance.now(), budget = limits(options);
  const selected = profileFor(options.profile);
  const frozen = selected.id === PROFILE;
  if (frozen) validateOptions(options);
  assert(budget.heapMb < budget.rssMb, 'Heap budget must be below RSS budget');
  assert(options.trace && options.out, 'Required: --trace --out');
  assert(['verify', 'record'].includes(options.mode ?? 'verify'), 'Expected --mode verify|record');
  const record = options.mode === 'record';
  let peakRssBytes = 0;
  const guard = () => {
    peakRssBytes = Math.max(peakRssBytes, process.memoryUsage.rss(), process.resourceUsage().maxRSS * 1024);
    assert(peakRssBytes <= budget.rssMb * 1024 ** 2, 'RSS budget exceeded');
    assert(performance.now() - started < budget.seconds * 1000, 'Time budget exceeded');
  };
  const output = path.resolve(options.out);
  const parent = fs.realpathSync(path.dirname(output));
  assert(!fs.existsSync(output), 'Output directory already exists');
  // lstat also rejects dangling symlinks; mkdir below reserves the name atomically.
  try { fs.lstatSync(output); assert.fail('Output directory already exists'); }
  catch (e) { if (e.code !== 'ENOENT') throw e; }
  const game = fs.realpathSync(options.game || fileURLToPath(new URL('../../Stronghold-Protocol', import.meta.url)));
  assert(parent !== game && !parent.startsWith(`${game}${path.sep}`), 'No output inside game tree / live indexes');
  assert(fs.statSync(options.trace).size <= 12_000_000, 'Trace exceeds 12 MB');
  const bytes = fs.readFileSync(options.trace), traceSHA256 = hash(bytes);
  assert.equal(traceSHA256, options.traceSha256 ?? TRACE_SHA256, 'Trace SHA256 mismatch');
  const trace = validateTrace(JSON.parse(bytes), options.profile);
  const metadata = frozen ? receiptMetadata(trace, traceSHA256, options) : null;
  const id = `${selected.idPrefix}-s${trace.terminal.episode.seed}-${traceSHA256.slice(0, 12)}`;
  const count = options.prefix === undefined ? trace.actions.length : Number(options.prefix);
  assert(Number.isInteger(count) && count >= 1 && count <= trace.actions.length, 'Invalid prefix count');
  const prefix = count < trace.actions.length;
  assert(!prefix || !record, 'Prefix diagnostics cannot export recordings or a victory episode');
  const env = await CoopEnv.create(game, configuration(trace));
  const { Battle } = await import(pathToFileURL(path.join(game, 'server/sim/Battle.js')));
  const recorder = new Recorder({ id, guard, record, maxBytes: budget.maxOutputMb * 1_000_000, frameEvery: budget.frameEvery, profile: options.profile });
  recorder.env = env;
  const OriginalMatch = env.Match;
  env.Match = class ObservedMatch extends OriginalMatch {
    constructor(opts) { super(opts); recorder.attach(this, Battle); }
  };
  let state, recordings, simulationMs;
  try {
    const simulationStart = performance.now();
    state = env.reset(trace.terminal.episode);
    verifyState(state, trace, options.profile);
    for (const [i, entry] of trace.actions.slice(0, count).entries()) {
      progress({ completedActions: i, nextRound: entry.round, nextAction: entry.action.type });
      guard(); verifyCandidate(state, entry, i);
      const first = recorder.events.length, candidateCount = state.candidates.length;
      recorder.traceIndex = i;
      state = env.step(entry.candidateIndex);
      recorder.traceIndex = null;
      recorder.check(); verifyState(state, trace, options.profile);
      const calls = recorder.events.slice(first).filter(e => e.playerId === 'learner' && e.source === 'policy' &&
        e.parentSeq === null && ['buy', 'sell', 'refresh', 'levelUp', 'setReady'].includes(e.method));
      assert.equal(calls.length, 1, `Actual learner action count at ${i}`);
      const call = calls[0];
      assert.equal(call.method, entry.action.type === 'ready' ? 'setReady' : entry.action.type);
      assert.equal(call.round, entry.round);
      assert.equal(call.accepted, true, `Actual action rejected at ${i}`);
      const action = entry.action;
      assert.deepEqual(call.action, action.type === 'buy' ? { t: 'g.buy', slot: action.slot }
        : action.type === 'sell' ? { t: 'g.sell', uid: action.uid }
          : action.type === 'ready' ? { t: 'g.ready', ready: true } : { t: `g.${action.type}` });
      call.policy = { traceIndex: i, candidateIndex: entry.candidateIndex, candidate: action, candidateCount };
    }
    if (!prefix) verifyTerminal(state, trace);
    recordings = prefix ? [] : recorder.finish(env);
    assert.equal(env.fatalRejections, 0);
    assert.equal(recorder.events.filter(e => e.policy).length, count);
    assert(recorder.events.every(e => e.source !== 'policy' || e.playerId === 'learner'), 'Partner mislabeled policy');
    simulationMs = performance.now() - simulationStart;
  } finally { env.close(); }
  guard();
  const sources = {};
  for (const dir of ['coop', 'rl', 'coop-replay', ...(frozen ? ['frozen-replay', 'prep-timing'] : [])]) {
    for (const name of fs.readdirSync(new URL(`../${dir}/`, import.meta.url)).filter(n => /\.(mjs|json|py)$/.test(n))) {
      sources[`${dir}/${name}`] = hash(fs.readFileSync(new URL(`../${dir}/${name}`, import.meta.url)));
    }
  }
  for (const name of ['record.mjs', 'replay.mjs', 'index.mjs'])
    sources[`model-replay/${name}`] = hash(fs.readFileSync(new URL(`../model-replay/${name}`, import.meta.url)));
  const provenance = { kind: selected.kind, behavior: trace.behavior,
    ...(metadata || { singleCheckpoint: null, inferenceRerun: false, trainingRecaptureIndependentlyVerified: false }),
    trace: { sha256: traceSHA256, path: path.resolve(options.trace), captureSourceSHA256: trace.captureSourceSHA256 },
    rules: RULES, controller: trace.controller, episode: trace.terminal.episode,
    node: process.version, sources, playerIdMap: PLAYER_MAP,
    observationScope: 'four-seat-private-offline-terminal-replay-only; never supplied to a model' };
  const report = { id, mode: prefix ? 'prefix-diagnostic' : record ? 'record' : 'verify', kind: selected.kind,
    verification: { fullCandidatesAndRounds: count, actualAcceptedLearnerCalls: count,
      completeTerminal: !prefix, completeSummary: !prefix, completePartnerAudit: !prefix,
      engineErrors: state.engineErrors, actionErrors: state.actionErrors, controllerErrors: state.controllerErrors },
    summary: state.summary, prefixProgress: prefix ? { completedActions: count, nextRound: state.rounds, done: state.done } : null,
    partnerActionSHA256: Object.fromEntries(Object.entries(state.partnerAudit || {}).map(([k, v]) => [k, v.actionSHA256])),
    policySteps: state.steps, methodCalls: recorder.events.length, provenance,
    capture: { enabled: record, fields: record ? 'all-real-fields-all-seats' : 'none',
      frameEvery: record ? budget.frameEvery : null, allEvents: record,
      battles: recordings.length, frames: recordings.reduce((n, r) => n + r.frames.length, 0),
      events: recordings.reduce((n, r) => n + r.frames.reduce((s, f) => s + f.ev.length, 0), 0) },
    limits: { ...budget, outputBytes: budget.maxOutputMb * 1_000_000 }, simulationMs,
    untested: ['native browser rendering', ...(record ? [] : ['server full recording/export'])] };
  const files = record ? bundle({ id, state, recorder, recordings, provenance, profile: options.profile, guard, maxBytes: budget.maxOutputMb * 1_000_000 }) : new Map();
  report.files = [...files].map(([file, b]) => ({ file, bytes: b.length, sha256: hash(b) }));
  report.payloadBytes = [...files.values()].reduce((n, b) => n + b.length, 0);
  report.elapsedMs = performance.now() - started;
  guard(); report.peakRssBytes = peakRssBytes;
  files.set('verification.json', Buffer.from(JSON.stringify(report, null, 2)));
  const totalBytes = [...files.values()].reduce((n, b) => n + b.length, 0);
  assert(totalBytes <= budget.maxOutputMb * 1_000_000, 'Output budget exceeded');
  guard(); fs.mkdirSync(output); // Never remove/reuse any existing path on failure.
  // Publish index last, after all data and verification. No live index changes.
  const names = [...files.keys()].filter(n => n !== 'agent/index.json');
  if (files.has('agent/index.json')) names.push('agent/index.json');
  for (const name of names) {
    guard(); fs.mkdirSync(path.dirname(path.join(output, name)), { recursive: true });
    fs.writeFileSync(path.join(output, name), files.get(name), { flag: 'wx' });
  }
  guard();
  return { out: output, id, mode: report.mode, victory: prefix ? null : state.victory, rounds: state.rounds, teamLp: state.summary?.teamLp ?? null,
    policySteps: state.steps, capture: report.capture, simulationMs, elapsedMs: performance.now() - started,
    peakRssBytes, totalBytes, verification: report.verification, partnerActionSHA256: report.partnerActionSHA256 };
}
