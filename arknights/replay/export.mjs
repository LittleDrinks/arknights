// Native replay artifacts. No training/profile or legacy runtime dependencies.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export const RULES = '6ab2468279e926d107788f63d7fe219b85f67b4a';
export const SCHEMA = 'arknights.bot-transitions.v1';
export const DEFAULT_MAX_BYTES = 512 * 1024 * 1024;
export const DEFAULT_PROFILE = Object.freeze({ kind: 'generic-evaluation', label: 'Evaluation',
  battleLabel: 'Evaluation', note: 'Actual evaluation calls and battles; private seat views are offline replay data only.' });
export const copy = value => value == null ? null : JSON.parse(JSON.stringify(value));
export function safeId(id) {
  assert(typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$/.test(id), 'Unsafe replay id');
  return id;
}
export function safeRelative(file) {
  assert(typeof file === 'string' && /^[A-Za-z0-9._/-]+$/.test(file) &&
    file.split('/').every(p => p && p !== '.' && p !== '..') && !file.startsWith('/'), 'Unsafe artifact path');
  return file;
}
export const PLAYER_MAP = Object.freeze({ learner: 'ai_0', partner1: 'ai_1', partner2: 'ai_2', partner3: 'ai_3',
  'partner-1': 'ai_1', 'partner-2': 'ai_2', 'partner-3': 'ai_3', ai0: 'ai_0', ai1: 'ai_1', ai2: 'ai_2', ai3: 'ai_3' });
export function viewerIds(value) {
  const rename = s => {
    if (PLAYER_MAP[s]) return PLAYER_MAP[s];
    for (const [id, mapped] of Object.entries(PLAYER_MAP))
      if (s.endsWith(`:${id}`)) return s.slice(0, -id.length) + mapped;
    return s;
  };
  if (typeof value === 'string') return rename(value);
  if (Array.isArray(value)) return value.map(viewerIds);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [rename(k), viewerIds(v)]));
  return value;
}

export function validateRecordings(recordings) {
  const names = new Set();
  for (const rec of recordings) {
    safeId(rec.name); assert(!names.has(rec.name), 'Recording collision'); names.add(rec.name);
    assert.equal(rec.version, 1); assert.equal(rec.tick, 1 / 30);
    assert(rec.field?.fieldId && rec.field.rect && Array.isArray(rec.field.units));
    assert(rec.participants?.length > 0); assert.equal(rec.frames[0]?.snap.t, 0, 'Missing initial frame');
    const units = new Set(rec.field.units.map(u => u.id));
    let previous = -1;
    for (const f of rec.frames) {
      assert.equal(f.snap.fieldId, rec.field.fieldId);
      assert(f.snap.t > previous && Array.isArray(f.snap.units) && Array.isArray(f.ev), 'Invalid frame');
      previous = f.snap.t;
      for (const ev of f.ev) {
        assert(Array.isArray(ev) && typeof ev[0] === 'string');
        if (ev[0] === 'spawn') units.add(ev[1].id);
      }
      for (const u of f.snap.units) assert(units.has(u[0]), `Missing spawn metadata ${u[0]}`);
    }
    assert(Math.abs(previous - rec.duration) <= 0.001, 'Missing final frame');
    assert.equal(rec.result?.errors, 0, 'Errored or unfinished real battle');
  }
}

// Native UI selects the first field of a round. Variants expose every actual field,
// including repeated IDs within a round, without duplicating recording files.
export function battleViews(refs) {
  const views = [{ suffix: '', label: '默认战场', refs }], covered = new Set();
  const cover = rows => { for (const round of new Set(rows.map(r => r.round))) covered.add(rows.find(r => r.round === round).name); };
  cover(refs);
  for (const fieldId of new Set(refs.map(r => r.fieldId))) {
    if (refs.filter(r => r.fieldId === fieldId).every(r => covered.has(r.name))) continue;
    const ordered = [...refs].sort((a, b) => a.round - b.round || Number(b.fieldId === fieldId) - Number(a.fieldId === fieldId));
    views.push({ suffix: `-view${views.length}`, label: `战场 ${fieldId}`, refs: ordered }); cover(ordered);
  }
  for (const ref of refs) if (!covered.has(ref.name)) {
    const ordered = [ref, ...refs.filter(r => r !== ref)];
    views.push({ suffix: `-view${views.length}`, label: `R${ref.round} ${ref.fieldId}`, refs: ordered }); cover(ordered);
  }
  assert.equal(covered.size, refs.length, 'Unreachable native battle');
  return views;
}

export function episodeSteps(events) {
  return viewerIds(events.map(e => ({ seq: e.seq, playerId: e.playerId, action: e.action,
    accepted: e.accepted, result: e.result, reward: e.reward, terminated: e.terminated,
    source: e.source, method: e.method, parentSeq: e.parentSeq, policy: e.policy,
    observation: e.terminal_observation || e.next_observation, methodObservation: e.next_observation,
    beforeObservation: e.observation, before: { phase: e.observation.phase, round: e.observation.round,
      funds: e.observation.own?.funds, lp: e.observation.own?.lp } })));
}

export function mergeIndexes(existingAgent, existingRecordings, addedAgent, addedRecordings) {
  assert.equal(existingAgent.schema, addedAgent.schema, 'Index schema mismatch');
  const append = (oldRows, newRows, key) => {
    const keys = new Set(), files = new Set();
    for (const row of [...oldRows, ...newRows]) {
      assert(typeof row[key] === 'string', 'Invalid index entry'); safeRelative(row.file);
      assert(!keys.has(row[key]) && !files.has(row.file), `Index collision: ${row[key]}`);
      keys.add(row[key]); files.add(row.file);
    }
    return copy([...oldRows, ...newRows]);
  };
  const episodes = append(existingAgent.episodes, addedAgent.episodes, 'id');
  const recordings = append(existingRecordings, addedRecordings, 'name');
  for (const ep of episodes) for (const b of ep.battles || [])
    assert(recordings.some(r => r.name === b.name && r.file === b.file), `Missing recording: ${b.name}`);
  return { agent: { ...copy(existingAgent), episodes, totalSteps: episodes.reduce((n, e) => n + e.steps, 0),
    totalBytes: episodes.reduce((n, e) => n + (e.bytes || 0), 0) }, recordings };
}

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
export function bundle({ id, state, recorder, recordings = recorder.battles, provenance = {},
  maxBytes = recorder.maxBytes ?? DEFAULT_MAX_BYTES, profile = recorder.profile ?? DEFAULT_PROFILE }) {
  safeId(id); recorder.check?.();
  const summary = state?.summary ?? state?.outcome?.summary;
  const terminal = state?.done ?? state?.terminal;
  assert(state && terminal !== false && (terminal === true || summary), 'Terminal state required');
  assert(!state.rules || state.rules === RULES, 'Pinned rules mismatch');
  validateRecordings(recordings);
  const steps = episodeSteps(recorder.events);
  const seats = recorder.playerIds ?? [...new Set(steps.map(s => s.playerId))];
  assert(seats.length > 0, 'No seats captured');
  for (const id of viewerIds(seats)) {
    const own = steps.filter(s => s.playerId === id), terminal = own.filter(s => s.terminated);
    assert.equal(terminal.length, 1, `Missing terminal for ${id}`);
    assert.equal(terminal[0].observation.phase, 'RESULT', 'Nonterminal private view');
    assert(own.every(s => s.observation?.own && typeof s.accepted === 'boolean' && s.action?.t));
    if (id !== 'ai_0') assert(own.every(s => s.source !== 'policy' && !s.policy), 'Partner labeled policy');
  }
  const refs = recordings.map(r => viewerIds({ name: r.name, title: r.title, file: `${r.name}.json`,
    kind: r.kind, stageId: r.stageId, round: r.round, duration: r.duration,
    fieldId: r.field.fieldId, participants: r.participants }));
  const selected = { ...DEFAULT_PROFILE, ...profile }, episode = state.episode ?? {};
  const actions = {};
  for (const e of recorder.events) actions[e.action.t] = (actions[e.action.t] || 0) + 1;
  const base = { config: `${episode.mode ?? state.mode ?? recorder.match?.mode ?? 'evaluation'}:${episode.difficulty ?? state.difficulty ?? recorder.match?.difficulty ?? ''}`,
    seed: episode.seed ?? state.seed ?? recorder.match?.seed, players: seats.length, commit: RULES,
    source: selected.kind, schema: SCHEMA, rehearsal: 0,
    observation_scope: 'all-seat-private-offline-terminal-replay-only', provenance,
    note: selected.note, victory: state.victory ?? summary?.victory,
    roundsPassed: state.rounds ?? summary?.roundsPassed ?? state.round ?? recorder.match?.round, hiddenReached: summary?.hiddenReached,
    policySteps: state.steps, steps: steps.length, methodCalls: recorder.events.filter(e => e.type === 'transition').length,
    commands: recorder.commands?.length ?? 0, accepted: steps.filter(s => s.accepted).length,
    engineErrors: state.engineErrors ?? summary?.errors, actions,
    terminalReason: summary?.reason, summary: viewerIds(summary ?? null),
    capture: { fields: 'all-real-fields-all-seats', allEvents: true, frameEvery: recorder.frameEvery } };
  const files = new Map(); let size = 0;
  const add = (name, value) => {
    recorder.check?.(); safeRelative(name); assert(!files.has(name), 'Artifact collision');
    const bytes = Buffer.isBuffer(value) ? value : Buffer.from(JSON.stringify(value));
    size += bytes.length; assert(size <= maxBytes, 'Output budget exceeded'); files.set(name, bytes);
  };
  for (const r of recordings) add(`recordings/${r.name}.json`, viewerIds(r));
  add('recordings/index.json', refs);
  const commandFile = `commands/${id}.json`;
  add(`agent/${commandFile}`, recorder.commands ?? []);
  const entries = [];
  for (const view of battleViews(refs)) {
    const entryId = safeId(id + view.suffix);
    const meta = { ...base, id: entryId, policyLabel: `${selected.label} · ${view.label}`, battles: view.refs };
    const file = `episodes/${entryId}.json`;
    add(`agent/${file}`, { meta, steps, engineCalls: steps.filter(s => s.source === 'engine'), commandFile: `../${commandFile}` });
    const { summary: _summary, ...indexMeta } = meta;
    entries.push({ ...indexMeta, file, bytes: files.get(`agent/${file}`).length });
  }
  const manifest = { schema: SCHEMA, commit: RULES, quality: selected.kind, episodes: entries,
    totalSteps: entries.reduce((n, e) => n + e.steps, 0), totalBytes: entries.reduce((n, e) => n + e.bytes, 0) };
  mergeIndexes({ schema: SCHEMA, episodes: [] }, [], manifest, refs);
  add('agent/index.json', manifest);
  const artifacts = Object.fromEntries([...files].sort(([a], [b]) => a.localeCompare(b))
    .map(([name, bytes]) => [name, { bytes: bytes.length, sha256: hash(bytes) }]));
  const artifactSHA = hash(JSON.stringify(artifacts));
  add('verification.json', { schema: 'arknights.replay-verification.v1', rules: RULES, id,
    artifactSHA, artifactSHA256: artifactSHA, artifacts,
    files: Object.entries(artifacts).map(([file, row]) => ({ file, ...row })),
    episodes: entries.length, recordings: refs.length,
    seats: viewerIds(seats), commands: recorder.commands?.length ?? 0, offlineOnly: true });
  return files;
}

// The parent must already exist. Claim a NEW directory and create every file with wx.
// A failed write removes only the directory this call claimed; existing data is untouched.
export function writeBundle(options) {
  const { recordDir } = options;
  assert(typeof recordDir === 'string' && recordDir.length, 'recordDir is required');
  const target = path.resolve(recordDir);
  assert(fs.statSync(path.dirname(target)).isDirectory(), 'recordDir parent must exist');
  assert(!fs.existsSync(target), 'recordDir must be new');
  const files = bundle(options);
  fs.mkdirSync(target);
  try {
    for (const [name, bytes] of files) {
      const file = path.join(target, safeRelative(name));
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, bytes, { flag: 'wx', mode: 0o600 });
    }
  } catch (error) { fs.rmSync(target, { recursive: true, force: true }); throw error; }
  const verification = JSON.parse(files.get('verification.json'));
  return { recordDir: target, bundleDir: target, id: options.id, artifactSHA: verification.artifactSHA,
    artifactSHA256: verification.artifactSHA, episodes: verification.episodes, recordings: verification.recordings,
    verification: path.join(target, 'verification.json') };
}
