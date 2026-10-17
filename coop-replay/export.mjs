import assert from 'node:assert/strict';
import { gzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { viewerIds } from './record.mjs';
import { KIND } from './verify.mjs';
export { mergeIndexes } from '../model-replay/index.mjs';
import { mergeIndexes } from '../model-replay/index.mjs';

export const SCHEMA = 'arknights.bot-transitions.v1';

export function validateRecordings(recordings) {
  assert(recordings.length, 'No real battles');
  for (const rec of recordings) {
    assert.equal(rec.version, 1);
    assert.equal(rec.tick, 1 / 30);
    assert(rec.field.fieldId && rec.field.rect && Array.isArray(rec.field.units));
    assert(rec.participants.length > 0);
    assert.equal(rec.frames[0]?.snap.t, 0, 'Missing initial frame');
    const units = new Set(rec.field.units.map(u => u.id));
    let previous = -1;
    for (const f of rec.frames) {
      assert.equal(f.snap.fieldId, rec.field.fieldId);
      assert(f.snap.t > previous && Array.isArray(f.snap.units) && Array.isArray(f.ev));
      previous = f.snap.t;
      for (const ev of f.ev) {
        assert(Array.isArray(ev) && typeof ev[0] === 'string');
        if (ev[0] === 'spawn') units.add(ev[1].id);
      }
      for (const u of f.snap.units) assert(units.has(u[0]), `Missing spawn metadata ${u[0]}`);
    }
    assert(Math.abs(previous - rec.duration) <= 0.001, 'Missing final frame');
    assert.equal(rec.result.errors, 0);
  }
}

export function episodeSteps(events) {
  return viewerIds(events.filter(e => e.source !== 'engine').map(e => ({ seq: e.seq, playerId: e.playerId,
    action: e.action, accepted: e.accepted, result: e.result, reward: e.reward, terminated: e.terminated,
    source: e.source, method: e.method, parentSeq: e.parentSeq, policy: e.policy,
    observation: e.terminal_observation || e.next_observation, methodObservation: e.next_observation,
    beforeObservation: e.observation, before: { phase: e.observation.phase, round: e.observation.round,
      funds: e.observation.own.funds, lp: e.observation.own.lp } })));
}

export function validateSteps(steps) {
  for (let seat = 0; seat < 4; seat++) {
    const own = steps.filter(s => s.playerId === `ai_${seat}`);
    assert(own.length && own.some(s => s.action.t === 'g.band'), `Missing seat ${seat} draft`);
    const terminal = own.filter(s => s.terminated);
    assert.equal(terminal.length, 1);
    assert.equal(terminal[0].observation.phase, 'RESULT');
    // Eliminated seats legitimately have no decisions in the final round.
    for (const s of own) {
      assert(s.action.t && typeof s.before.round === 'number' && typeof s.accepted === 'boolean');
      assert(s.observation.own && s.methodObservation.own && s.beforeObservation.own);
      assert(s.observation.public.players.some(p => p.playerId === s.playerId));
      if (seat) assert(s.source !== 'policy' && !s.policy, 'Teammate labeled as model');
    }
  }
}

// The unmodified UI finds only the first battle in a round, regardless of selected
// player. Native-schema variants put each field first; actual recording files are shared.
export function battleViews(refs) {
  const views = [{ suffix: '', label: '默认战场', refs }];
  const covered = new Set();
  const cover = rows => { for (const round of new Set(rows.map(r => r.round))) covered.add(rows.find(r => r.round === round).name); };
  cover(refs);
  for (const fieldId of new Set(refs.map(r => r.fieldId))) {
    if (refs.filter(r => r.fieldId === fieldId).every(r => covered.has(r.name))) continue;
    const ordered = [...refs].sort((a, b) => a.round - b.round || Number(b.fieldId === fieldId) - Number(a.fieldId === fieldId));
    views.push({ suffix: `-view${views.length}`, label: `战场 ${fieldId}`, refs: ordered }); cover(ordered);
  }
  // Also cover repeated field IDs in the same round, should an engine ever create them.
  for (const ref of refs) if (!covered.has(ref.name)) {
    const ordered = [ref, ...refs.filter(r => r !== ref)];
    views.push({ suffix: `-view${views.length}`, label: `R${ref.round} ${ref.fieldId}`, refs: ordered }); cover(ordered);
  }
  assert.equal(covered.size, refs.length, 'A battle is unreachable via the native UI');
  return views;
}

export function bundle({ id, state, recorder, recordings, provenance, guard = () => {}, maxBytes }) {
  validateRecordings(recordings);
  const steps = episodeSteps(recorder.events); validateSteps(steps);
  const viewerRecordings = viewerIds(recordings);
  const refs = viewerRecordings.map(r => ({ name: r.name, title: r.title, file: `${r.name}.json`,
    kind: r.kind, stageId: r.stageId, round: r.round, duration: r.duration,
    fieldId: r.field.fieldId, participants: r.participants }));
  const engineCalls = viewerIds(recorder.events.filter(e => e.source === 'engine'));
  const actions = {};
  for (const e of recorder.events) actions[e.action.t] = (actions[e.action.t] || 0) + 1;
  const base = { config: `coop:${state.episode.difficulty}`, seed: state.episode.seed, players: 4,
    commit: state.rules, source: KIND, rehearsal: 0, schema: SCHEMA,
    observation_scope: 'four-seat-private-offline-terminal-replay-only', provenance,
    note: '真实训练轨迹跨多个 PPO 行为版本；不是冻结 checkpoint 成功。四席私有视图仅用于离线终局回放。',
    victory: state.victory, roundsPassed: state.rounds, hiddenReached: state.summary.hiddenReached,
    policySteps: state.steps, steps: steps.length, methodCalls: recorder.events.length,
    accepted: steps.filter(e => e.accepted).length, engineErrors: state.engineErrors, schedulerErrors: 0,
    actions, terminalReason: state.summary.reason, summary: viewerIds(state.summary),
    capture: { fields: 'all-real-fields-all-seats', allEvents: true, frameEvery: recorder.frameEvery,
      explanation: `真实 t=0、每 ${recorder.frameEvery} ticks 及结束帧；全部事件保留。战场随 episode 入口选择，不随私有席位切换。` } };
  const files = new Map(); let size = 0;
  const add = (name, bytes) => {
    guard(); assert(!files.has(name));
    const b = Buffer.isBuffer(bytes) ? bytes : Buffer.from(JSON.stringify(bytes));
    size += b.length; assert(size <= maxBytes, 'Output budget exceeded'); files.set(name, b);
  };
  for (const r of viewerRecordings) add(`recordings/${r.name}.json`, r);
  add('recordings/index.json', refs);
  const transitions = recorder.events.map(e => JSON.stringify(e)).join('\n');
  const transitionHash = createHash('sha256').update(transitions).digest('hex');
  const entries = [];
  for (const view of battleViews(refs)) {
    const entryId = id + view.suffix;
    const meta = { ...base, id: entryId, policyLabel: `合作训练真实轨迹 · ${view.label}`, battles: view.refs };
    add(`agent/episodes/${entryId}.json`, { meta, steps, engineCalls });
    // Download keeps literal engine IDs, including the full original terminal.
    const download = gzipSync(`${JSON.stringify({ type: 'episode', ...meta })}\n${transitions}\n${JSON.stringify({ type: 'terminal', ...state })}\n`);
    add(`agent/downloads/${entryId}.jsonl.gz`, download);
    const { summary: _summary, ...indexMeta } = meta;
    entries.push({ ...indexMeta, bytes: download.length, transitionHash, file: `episodes/${entryId}.json` });
  }
  const manifest = { schema: SCHEMA, createdAt: new Date().toISOString(), commit: state.rules,
    quality: KIND, episodes: entries, totalSteps: entries.reduce((n, e) => n + e.steps, 0),
    totalBytes: entries.reduce((n, e) => n + e.bytes, 0) };
  mergeIndexes({ schema: SCHEMA, episodes: [] }, [], manifest, refs);
  add('agent/index.json', manifest);
  return files;
}
