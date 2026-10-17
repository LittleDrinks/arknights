#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { gunzipSync, gzipSync } from 'node:zlib';
import { RULES } from '../rl/env.mjs';
import { makeSchema } from '../rl/features.mjs';
import { exact, reduceEvents } from './core.mjs';

export const ROOT = fileURLToPath(new URL('../', import.meta.url));
export const BASE = 'ce9c4c6f624b1093872b3192db95a122aafaf0e0';
export const DEFAULT_RAW = path.resolve(ROOT, '../experiments/coop-training-win-50003.jsonl.gz');
export const DEFAULT_REFERENCE = path.resolve(ROOT, '../experiments/coop-training-win-primary-labels-reference.jsonl.gz');
export const DEFAULT_GAME = path.resolve(ROOT, '../Stronghold-Protocol');
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const ids = ['learner', 'partner-1', 'partner-2', 'partner-3'];
const git = (dir, ...args) => execFileSync('git', ['-C', dir, ...args], { encoding: 'utf8', timeout: 5000 }).trim();

export function guardSince(start = performance.now()) {
  return () => {
    assert(performance.now() - start < 20_000, '20 second extraction budget exceeded');
    assert(process.resourceUsage().maxRSS * 1024 <= 768 * 1024 * 1024, '768 MiB RSS budget exceeded');
  };
}

export function readJsonl(file, maxExpanded = 64 * 1024 * 1024) {
  const size = fs.statSync(file).size;
  assert(size <= 5_000_000, `input compressed size limit: ${file}`);
  const packed = fs.readFileSync(file), bytes = gunzipSync(packed, { maxOutputLength: maxExpanded });
  const text = bytes.toString('utf8');
  assert(text.endsWith('\n'), `truncated JSONL: ${file}`);
  const lines = text.slice(0, -1).split('\n');
  assert(lines.every(Boolean), `empty JSONL record: ${file}`);
  return { rows: lines.map(JSON.parse), lines,
    source: { path: path.resolve(file), bytes: size, sha256: sha(packed), payloadBytes: bytes.length, payloadSHA256: sha(bytes) } };
}

export async function staticData(game, header) {
  assert.equal(git(game, 'rev-parse', 'HEAD'), RULES, 'game rule pin');
  assert.equal(git(game, 'status', '--porcelain', '--', 'server', 'shared', 'data'), '', 'game source/data must be clean, including untracked files');
  const sources = {};
  for (const file of ['rl/env.mjs', 'rl/features.mjs', 'rl/controller.mjs', 'coop/env.mjs', 'coop-replay/record.mjs']) {
    const bytes = fs.readFileSync(path.join(ROOT, file));
    const pinned = execFileSync('git', ['-C', ROOT, 'show', `${BASE}:${file}`]);
    assert(bytes.equals(pinned), `changed pinned source ${file}`);
    sources[file] = sha(bytes);
    assert.equal(sources[file], header.provenance.sources[file], `capture source drift: ${file}`);
  }
  // Only static loaders are imported; no Match, Battle, reset, step, newMatch or simulator.
  const [{ getData }, { GameData }] = await Promise.all([
    import(pathToFileURL(path.join(game, 'server/data.js'))),
    import(pathToFileURL(path.join(game, 'server/match/gamedata.js'))),
  ]);
  const fail = message => { throw new Error(message); };
  const data = getData({ log: { info() {}, warn: fail, error: fail } });
  for (const k of ['chess', 'items', 'bonds', 'bands', 'config']) assert(data[k], `missing static ${k}`);
  return { data, gd: new GameData(data, 'mode_multi_abyss'), sources, dataSHA256: sha(JSON.stringify(data)) };
}

function originalIds(value) {
  if (typeof value === 'string') return /^ai_[0-3]$/.test(value) ? ids[Number(value.at(-1))] : value;
  if (Array.isArray(value)) return value.map(originalIds);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [originalIds(k), originalIds(v)]));
  return value;
}

export function validateRaw(rows) {
  const header = rows[0], terminal = rows.at(-1), events = rows.slice(1, -1);
  assert.equal(header.type, 'episode'); assert.equal(terminal.type, 'terminal');
  assert.equal(header.schema, 'arknights.bot-transitions.v1');
  assert.equal(header.commit, RULES); assert.equal(terminal.rules, RULES);
  assert.equal(header.provenance.rules, RULES);
  assert.equal(header.rehearsal, 0); assert.equal(header.players, 4);
  assert.equal(header.seed, 50003); assert.equal(terminal.episode.seed, header.seed);
  assert.equal(header.config, 'coop:ABYSS');
  assert.equal(terminal.episode.mode, 'coop'); assert.equal(terminal.episode.difficulty, 'ABYSS');
  assert.equal(terminal.episode.budget, 128);
  assert.equal(terminal.done, true); assert.equal(terminal.victory, true);
  assert.equal(terminal.summary.victory, true); assert.equal(terminal.summary.reason, 'victory');
  assert.equal(terminal.summary.roundsPassed, 14); assert.equal(terminal.rounds, 14);
  assert.equal(terminal.observation.public.phase, 'RESULT');
  assert.equal(terminal.summary.modeId, 'mode_multi_abyss');
  assert.equal(terminal.summary.difficulty, 'ABYSS'); assert.equal(terminal.summary.seed, header.seed);
  for (const key of ['engineErrors', 'actionErrors']) assert.equal(terminal[key], 0, `terminal ${key}`);
  assert.equal(terminal.summary.errors, 0);
  assert.equal(header.engineErrors, 0); assert.equal(header.schedulerErrors, 0);
  assert.equal(terminal.controllerErrors, terminal.controllerDiagnosticErrors);
  exact(originalIds(header.summary), terminal.summary, 'header summary aliases vs raw terminal');
  exact(header.provenance.episode, terminal.episode, 'episode provenance');
  assert.equal(header.victory, terminal.victory); assert.equal(header.roundsPassed, terminal.rounds);
  assert.equal(header.terminalReason, terminal.summary.reason);
  assert.equal(header.policySteps, 68); assert.equal(terminal.steps, 68);
  assert.equal(header.methodCalls, events.length);
  assert.equal(header.steps, events.filter(e => e.source !== 'engine').length);
  assert.equal(header.accepted, events.filter(e => e.source !== 'engine' && e.accepted).length);
  const counts = {};
  for (const e of events) {
    assert.equal(e.type, 'transition'); assert(ids.includes(e.playerId), `unknown seat ${e.playerId}`);
    assert(!e.exception, `exception at seq ${e.seq}: ${e.exception}`);
    assert.equal(typeof e.accepted, 'boolean'); assert.equal(typeof e.terminated, 'boolean');
    assert.equal(e.accepted, e.result?.ok === true, `accepted/result mismatch ${e.seq}`);
    assert(e.action?.t, `missing raw action ${e.seq}`);
    for (const key of ['observation', 'next_observation']) {
      const obs = e[key];
      assert(obs?.own && obs.public, `raw ${key} missing at ${e.seq}; viewer format unsupported`);
      assert.equal(obs.own.playerId, e.playerId, `private seat mismatch ${e.seq}`);
      assert.equal(obs.own.seat, ids.indexOf(e.playerId));
      assert.equal(obs.mode, 'coop'); assert.equal(obs.public.modeId, 'mode_multi_abyss');
      assert.equal(obs.round, obs.public.round); assert.equal(obs.phase, obs.public.phase);
      assert(!obs.history, 'input history must not be used');
      assert.equal(Object.keys(obs.own.diy || {}).length, 0, `unsupported per-seat DIY GameData overlay at ${e.seq}`);
      assert.equal((obs.own.standIns || []).length, 0, `unsupported stand-in overlay at ${e.seq}`);
    }
    assert.equal(e.round, e.observation.public.round);
    if (e.policy) {
      assert.equal(e.playerId, 'learner'); assert.equal(e.source, 'policy'); assert.equal(e.parentSeq, null);
      assert.equal(e.traceIndex, e.policy.traceIndex); assert(e.accepted);
    }
    if (e.playerId === 'learner' && e.source === 'policy' && e.parentSeq === null &&
        ['buy', 'sell', 'refresh', 'levelUp', 'setReady'].includes(e.method)) assert(e.policy, `missing policy binding ${e.seq}`);
    counts[e.action.t] = (counts[e.action.t] || 0) + 1;
  }
  exact(counts, header.actions, 'full raw method histogram');
  assert.equal(events.filter(e => e.policy).length, 68);
  assert.equal(terminal.summary.players.length, 4);
  for (const [seat, id] of ids.entries()) {
    const player = terminal.summary.players.find(p => p.playerId === id);
    assert.equal(player?.seat, seat);
    const own = events.filter(e => e.playerId === id);
    assert(own.length && own.some(e => e.method === '_applyBand'), `missing seat draft ${id}`);
    const ended = own.filter(e => e.terminated);
    assert.equal(ended.length, 1, `terminal marker ${id}`);
    const last = own.findLast(e => e.parentSeq === null && e.source !== 'engine');
    assert.equal(ended[0], last, `bad terminal tail ${id}`);
    const final = last.terminal_observation;
    assert.equal(final?.public.phase, 'RESULT'); assert.equal(final.public.round, 14);
    assert.equal(final.own.playerId, id); assert.equal(final.own.alive, player.alive);
    assert.equal(final.own.lp, player.lp);
    const positive = id === 'partner-1' || id === 'partner-2';
    assert.equal(player.victory, positive); assert.equal(player.alive, positive);
    assert.equal(player.roundsPassed, positive ? 14 : 10);
    assert.equal(player.eliminatedRound, positive ? null : 11);
  }
  return { header, terminal, events };
}

export async function extract({ raw = DEFAULT_RAW, reference = DEFAULT_REFERENCE, game = DEFAULT_GAME } = {}) {
  const started = performance.now(), guard = guardSince(started);
  const input = readJsonl(raw), ref = readJsonl(reference, 16 * 1024 * 1024);
  guard();
  const { header, terminal, events } = validateRaw(input.rows);
  assert.equal(ref.rows.length, 68, 'exact-reference must have all 68 primary samples');
  const { data, gd, sources, dataSHA256 } = await staticData(game, header);
  const schema = makeSchema(data);
  exact(schema, terminal.schema, 'static schema vs captured schema');
  const samples = [], labels = [], primary = [], perSeat = {};
  for (const p of terminal.summary.players) perSeat[p.playerId] = { seat: p.seat, individualVictory: p.victory,
    teamVictory: terminal.summary.victory, alive: p.alive, roundsPassed: p.roundsPassed,
    eliminatedRound: p.eliminatedRound, positive: p.seat === 1 || p.seat === 2, samples: 0 };
  // Reduction sees only events, static data and the fixed budget. No reference or terminal features.
  const reduced = reduceEvents(events, { gd, schema, budget: terminal.episode.budget, guard,
    onDecision(e, sample, cs, candidate, history) {
      if (e.policy) {
        assert.equal(e.policy.traceIndex, primary.length, `policy order ${e.seq}`);
        exact(sample, ref.rows[primary.length], `primary step=${primary.length} seq=${e.seq}`);
        primary.push({ seq: e.seq, traceIndex: e.policy.traceIndex });
      } else if (perSeat[e.playerId].positive) {
        const index = samples.length;
        samples.push(sample); perSeat[e.playerId].samples++;
        labels.push({ sample: index, seq: e.seq, parentSeq: e.parentSeq, playerId: e.playerId,
          seat: perSeat[e.playerId].seat, round: e.round, source: e.source, method: e.method,
          rawAction: e.action, candidateIndex: sample.action, candidate, candidates: cs,
          candidateSHA256: sha(JSON.stringify(cs)), sampleSHA256: sha(JSON.stringify(sample)),
          counterMode: history.mode, prepSteps: history.h.prepSteps, prepBudget: history.h.prepBudget });
      }
    } });
  assert.equal(primary.length, 68, 'primary strict comparison incomplete');
  assert(perSeat['partner-1'].samples && perSeat['partner-2'].samples, 'both survivors required');
  assert(!perSeat.learner.samples && !perSeat['partner-3'].samples);
  const dispositions = reduced.dispositions.map(d => ({ ...d,
    selection: labels.some(l => l.seq === d.seq) ? 'positive-label'
      : d.reason === 'legal-decision' ? 'excluded-seat' : d.reason }));
  const counters = Object.fromEntries([...reduced.histories].map(([id, h]) => [id,
    { mode: h.mode, steps: h.steps, prepRound: h.prepRound, history: h.h }]));
  // ShopEnv.view observes once more at RESULT after the primary dies. Project the
  // round reset using the last event's public clock, without reading terminal/private features.
  const finalRound = events.at(-1).next_observation.public.round;
  if (counters.learner.prepRound !== finalRound) {
    counters.learner.prepRound = finalRound;
    counters.learner.history.prepSteps = 0;
    for (const p of Object.values(counters.learner.history.pieces)) { p.refreshes = 0; p.boardRefreshes = 0; }
  }
  // Final primary counters independently cross-check raw terminal; terminal is never an encoder input.
  for (const k of ['totals', 'byId', 'rounds', 'pieces', 'prepSteps', 'prepBudget'])
    exact(counters.learner.history[k], terminal.observation.history[k], `terminal primary history.${k}`);
  guard();
  const metadata = { format: 'winning-labels/v1', base: BASE, rules: RULES, schema, mode: 'coop', difficulty: 'ABYSS',
    independentEpisodes: 1, positiveSeatSequences: 2, samples: samples.length,
    traceSHA256: header.provenance.trace.sha256, episodeGroup: header.provenance.trace.sha256,
    raw: input.source, reference: { ...ref.source, role: 'primary-exact-validation-only-never-teammate-labels' },
    transitionPayloadSHA256: sha(input.lines.slice(1, -1).join('\n')),
    provenance: header.provenance, staticData: { path: path.resolve(game), rules: RULES, dataSHA256 }, sources,
    implementationSources: Object.fromEntries(['core.mjs', 'extract.mjs'].map(name =>
      [`winning-labels/${name}`, sha(fs.readFileSync(path.join(ROOT, 'winning-labels', name)))])),
    seats: perSeat, primaryValidation: { exact: true, samples: primary.length, fields: 'every feature scalar, candidate order, mask, action; zero tolerance', primary },
    counters: Object.fromEntries(Object.entries(counters).map(([id, v]) => [id, { mode: v.mode, steps: v.steps,
      prepRound: v.prepRound, ...Object.fromEntries(['totals', 'byId', 'rounds', 'pieces', 'prepSteps', 'prepBudget'].map(k => [k, v.history[k]])) }])),
    limitations: ['One natural team victory; two correlated survivor sequences, never two independent games.',
      'Primary reference validates reconstruction; it is not a positive training source.',
      'Teammate teacher prepSteps are hypothetical observed accepted partner shopping calls, not an actually installed teacher environment.',
      'Own method-entry and accepted method-exit snapshots only; resolveTemp has no independent observe or shopping increment.',
      'Nonempty DIY/stand-in overlays are rejected. This extractor is scoped to the supplied four-seat ABYSS seed 50003 raw capture.',
      'Accepted nested shop methods affect totals; primary budget steps require an explicit policy binding. Cleanup-descendant methods are not teacher decisions.',
      'L.learner/bc_update compatibility is a tensor contract, not evidence of expert generalization. No training or simulation performed.'],
    terminal, header,
  };
  const audit = { ok: true, primaryExact: primary.length, samples: samples.length, independentEpisodes: 1,
    seats: perSeat, transitions: events.length, rejected: events.filter(e => !e.accepted).length,
    dispositionCounts: Object.fromEntries([...new Set(dispositions.map(d => d.selection))].map(k => [k, dispositions.filter(d => d.selection === k).length])),
    rawSHA256: input.source.sha256, referenceSHA256: ref.source.sha256,
    elapsedMs: Math.round(performance.now() - started), maxRSSMiB: process.resourceUsage().maxRSS / 1024 };
  return { samples, labels, dispositions, metadata, audit, guard };
}

export function outputFiles(result) {
  const jsonl = xs => xs.map(x => JSON.stringify(x)).join('\n') + '\n';
  const files = new Map([
    ['samples.jsonl.gz', gzipSync(jsonl(result.samples))],
    ['labels.jsonl.gz', gzipSync(jsonl(result.labels))],
    ['dispositions.jsonl.gz', gzipSync(jsonl(result.dispositions))],
    ['metadata.json.gz', gzipSync(JSON.stringify(result.metadata) + '\n')],
  ]);
  const manifest = { ...result.audit, files: Object.fromEntries([...files].map(([name, b]) => [name, { bytes: b.length, sha256: sha(b) }])) };
  files.set('audit.json', Buffer.from(JSON.stringify(manifest, null, 2) + '\n'));
  assert([...files.values()].reduce((n, b) => n + b.length, 0) <= 5_000_000, '5 MB total output budget exceeded');
  result.guard();
  return files;
}

export function writeNewDirectory(out, files) {
  const root = fs.realpathSync(path.join(ROOT, 'winning-labels'));
  const parent = fs.realpathSync(path.dirname(path.resolve(out)));
  assert(parent === root || parent.startsWith(root + path.sep), 'output must stay inside winning-labels/');
  // Atomic exclusive directory creation. Existing directories, files and symlinks all fail.
  fs.mkdirSync(out);
  for (const [name, bytes] of files) fs.writeFileSync(path.join(out, name), bytes, { flag: 'wx', mode: 0o600 });
}

async function main() {
  const opts = {}, args = process.argv.slice(2); let auditOnly = false, out;
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--audit') auditOnly = true;
    else if (['--raw', '--reference', '--game', '--out'].includes(a)) {
      assert(args[i + 1] && !args[i + 1].startsWith('--'), `missing value ${a}`);
      const v = path.resolve(args[++i]); if (a === '--out') out = v; else opts[a.slice(2)] = v;
    } else if (a === '--help') {
      console.log('node winning-labels/extract.mjs [--audit | --out NEW_DIRECTORY] [--raw RAW.gz] [--reference REF.gz] [--game PINNED_GAME]\nDefault: create a fresh winning-labels/output-<timestamp>-<pid> directory. Audit writes nothing.'); return;
    } else throw new Error(`unknown option ${a}`);
  }
  assert(!(auditOnly && out), '--audit cannot write --out');
  out ||= path.join(ROOT, 'winning-labels', `output-${Date.now()}-${process.pid}`);
  if (!auditOnly) assert(!fs.existsSync(out), 'output already exists; no overwrite');
  const result = await extract(opts), files = outputFiles(result);
  if (!auditOnly) writeNewDirectory(out, files);
  result.guard();
  console.log(JSON.stringify({ ...result.audit, outputBytes: [...files.values()].reduce((n, b) => n + b.length, 0), output: auditOnly ? null : out }));
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(e => { console.error(e.message); process.exitCode = 1; });
}
