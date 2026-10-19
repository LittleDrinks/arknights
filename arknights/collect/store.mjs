import fs from 'node:fs';
import path from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { RULES, safeId } from '../replay/export.mjs';
import { redact, project, actionOf, PROJECTION } from './projection.mjs';
export const SCHEMA = 'arknights.human-collection.v1';
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
function atomic(file, value) {
  const tmp = file + '.' + randomUUID() + '.tmp';
  const fd = fs.openSync(tmp, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(value, null, 2)); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(tmp, file);
}
export class Collections {
  constructor(root, { recover = false } = {}) {
    this.root = path.resolve(root, 'results/collections'); this.failed = new Map(); this.chains = new Map();
    fs.mkdirSync(this.root, { recursive: true, mode: 0o700 });
    if (recover) for (const c of this.list()) for (const m of c.matches) if (m.status === 'recording') {
      m.status = 'partial'; m.complete = false; m.victory = null; m.failure = 'collector interrupted before terminal seal';
      atomic(this.file(c.id, 'manifest.json'), c);
    }
    if (recover) for (const c of this.list()) this.recoverBattles(c.id);
  }
  file(id, name = 'manifest.json') { safeId(id); return path.join(this.root, id, name); }
  get(id) { return read(this.file(id)); }
  list() { return fs.readdirSync(this.root).filter(id => /^c-[\w-]+$/.test(id)).map(id => this.get(id)); }
  create(label, metadata = {}) {
    const id = 'c-' + randomUUID(), value = { ...metadata, id, schema: SCHEMA, rules: RULES, projection: PROJECTION,
      label: String(label || '真人采集').slice(0, 120), created: new Date().toISOString(), matches: [] };
    fs.mkdirSync(path.join(this.root, id), { mode: 0o700 });
    for (const channel of ['players', 'verification', 'annotations']) fs.writeFileSync(this.file(id, channel + '.jsonl'), '', { flag: 'wx', mode: 0o600 });
    atomic(this.file(id), value); return value;
  }
  fail(id, failure) {
    this.failed.set(id, failure);
    try { atomic(this.file(id, 'failure.json'), { failure }); } catch { /* interrupted/full storage cannot certify a capture */ }
  }
  append(id, channel, row) {
    try {
      const file = this.file(id, channel + '.jsonl');
      if (this.failed.has(id)) return false;
      const key = id + ':' + channel;
      let chain = this.chains.get(key);
      if (!chain) { const rows = this.rows(id, channel); chain = { seq: rows.length, hash: rows.at(-1)?.chain.hash || null }; }
      const data = redact({ eventId: randomUUID(), at: Date.now(), mono: performance.now(), ...row });
      const next = { seq: chain.seq + 1, previous: chain.hash, hash: sha(JSON.stringify([chain.hash, data])) };
      const fd = fs.openSync(file, 'a');
      try { fs.writeFileSync(fd, JSON.stringify({ ...data, chain: next }) + '\n'); fs.fsyncSync(fd); this.chains.set(key, { seq: next.seq, hash: next.hash }); }
      finally { fs.closeSync(fd); }
      return true;
    } catch (e) {
      const failure = 'capture write failed: ' + (e.code || 'integrity'); this.fail(id, failure);
      return false;
    }
  }
  rows(id, channel) {
    const rows = fs.readFileSync(this.file(id, channel + '.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
    let previous = null;
    for (const [i, row] of rows.entries()) {
      const { chain, ...data } = row;
      if (!chain || chain.seq !== i + 1 || chain.previous !== previous || chain.hash !== sha(JSON.stringify([previous, data]))) throw Error('Capture journal integrity failure');
      previous = chain.hash;
    }
    return rows;
  }
  battleFrame(id, record, frame) {
    const dir = this.file(id, 'battles'); fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    const meta = path.join(dir, record.name + '.metadata');
    if (!fs.existsSync(meta)) {
      const { frames, ...metadata } = record;
      atomic(meta, metadata);
    }
    const fd = fs.openSync(path.join(dir, record.name + '.frames.jsonl'), 'a', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify(frame) + '\n'); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
  }
  saveBattle(id, record, complete) {
    const file = this.file(id, 'battles/' + record.name + '.json');
    if (fs.existsSync(file)) throw Error('Battle recording already exists');
    atomic(file, { ...record, complete });
    for (const suffix of ['.metadata', '.frames.jsonl']) fs.rmSync(this.file(id, 'battles/' + record.name + suffix), { force: true });
  }
  recoverBattles(id) {
    const dir = this.file(id, 'battles');
    if (!fs.existsSync(dir)) return;
    for (const name of fs.readdirSync(dir).filter(n => n.endsWith('.metadata'))) {
      const meta = read(path.join(dir, name)), journal = path.join(dir, meta.name + '.frames.jsonl');
      if (fs.existsSync(path.join(dir, meta.name + '.json')) || !fs.existsSync(journal)) continue;
      const lines = fs.readFileSync(journal, 'utf8').split('\n'); lines.pop();
      const frames = [];
      for (const line of lines) {
        const frame = JSON.parse(line), last = frames.at(-1);
        if (last?.snap.t === frame.snap.t) { last.snap = frame.snap; last.ev.push(...frame.ev); }
        else frames.push(frame);
      }
      // Keep the original interrupted journal as evidence; publish a read-only partial recording.
      atomic(path.join(dir, meta.name + '.json'), { ...meta, frames, complete: false,
        duration: frames.at(-1)?.snap.t || 0, result: null, interruption: 'server restarted before battle completion' });
    }
  }
  match(id, meta) {
    const c = this.get(id); c.matches.push({ ...meta, status: 'recording', complete: false, victory: null,
      source: 'human-browser', replayMode: 'recorded-events', combatMode: 'server', scheduler: 'RealScheduler' });
    atomic(this.file(id), c);
    this.append(id, 'verification', { kind: 'match-start', matchId: meta.id, ...meta });
  }
  finish(id, matchId, outcome, failure = null) {
    const c = this.get(id), m = c.matches.find(m => m.id === matchId);
    if (!m || m.status !== 'recording') return;
    failure ||= this.failed.get(id) || null;
    const technical = failure || outcome?.errors || outcome?.simErrors || outcome?.reason === 'error';
    const complete = !!outcome && !technical && !['abandoned', 'left', 'timeout'].includes(outcome.reason);
    Object.assign(m, { status: technical ? 'technical-failure' : complete ? 'complete' : 'partial',
      complete, victory: complete ? outcome.victory === true : null, outcome: redact(outcome), failure });
    this.append(id, 'verification', { kind: 'match-end', matchId, outcome, failure, complete });
    atomic(this.file(id), c);
  }
  annotate(id, input) {
    const { matchId, seat, start, end, goalCore = '', goalSupport = '', stage, quality, note = '', author = 'organizer', annotationId } = input;
    const m = this.get(id).matches.find(m => m.id === matchId);
    if (!m || !m.seats.some(p => p.seat === seat)) throw Error('Unknown match/seat');
    const rows = this.rows(id, 'players').filter(r => r.matchId === matchId && r.seat === seat);
    const ids = rows.map(r => r.eventId);
    if (!ids.includes(start) || !ids.includes(end) || ids.indexOf(start) > ids.indexOf(end)) throw Error('Invalid event interval');
    if (!['prep', 'stack', 'convert', 'combat', 'other'].includes(stage) || !['great', 'usable', 'mistake', 'uncertain'].includes(quality)) throw Error('Invalid stage/quality');
    for (const v of [goalCore, goalSupport, note, author]) if (typeof v !== 'string' || v.length > 1000) throw Error('Invalid annotation text');
    const all = this.rows(id, 'annotations'), previous = annotationId && all.filter(a => a.annotationId === annotationId);
    if (annotationId && (!previous.length || previous.at(-1).matchId !== matchId || previous.at(-1).seat !== seat)) throw Error('Unknown annotation');
    const row = { matchId, seat, start, end, goalCore, goalSupport, stage, quality, note, author,
      annotationId: annotationId || randomUUID(), revision: annotationId ? previous.length + 1 : 1 };
    if (!this.append(id, 'annotations', row)) throw Error('Annotation write failed'); return row;
  }
  export(id, profile = 'human-reviewed') {
    if (!['human-reviewed', 'all-human'].includes(profile)) throw Error('Unknown export profile');
    const collection = this.get(id);
    if (collection.rules !== RULES || collection.projection !== PROJECTION || collection.schema !== SCHEMA) throw Error('Collection version mismatch');
    if (this.failed.has(id) || fs.existsSync(this.file(id, 'failure.json'))) throw Error('Capture storage failed');
    const players = this.rows(id, 'players'), verification = this.rows(id, 'verification');
    const revisions = this.rows(id, 'annotations'), latest = [...new Map(revisions.map(a => [a.annotationId, a])).values()];
    const samples = [], coverage = {}, exclusions = {};
    const exclude = reason => exclusions[reason] = (exclusions[reason] || 0) + 1;
    for (const r of players.filter(r => r.kind === 'request')) {
      const m = collection.matches.find(m => m.id === r.matchId);
      const action = actionOf(r.message);
      const v = verification.find(v => v.kind === 'handled' && v.requestId === r.requestId);
      const ack = players.findLast(p => p.kind === 'ack' && p.requestId === r.requestId);
      const seatOutcome = m?.outcome?.players?.find(p => p.seat === r.seat || p.playerId === r.playerId);
      const field = coverage[r.message.t] ||= { requests: 0, serverAccepted: 0, parameterized: 0, shortlisted: null, missingFields: [] };
      field.requests++;
      if (v?.status === 'accepted') { field.serverAccepted++; if (action) field.parameterized++; }
      if (!action) field.missingFields = ['unrepresented protocol action'];
      if (!m || ['recording', 'technical-failure'].includes(m.status)) { exclude('unsealed-or-failed'); continue; }
      if (v && (v.matchId !== m.id || v.playerId !== r.playerId || v.epoch !== r.epoch || JSON.stringify(actionOf(v.action)) !== JSON.stringify(action))) throw Error('Execution identity mismatch');
      if (r.source !== 'human' || v?.status !== 'accepted' || ack?.status !== 'accepted') { exclude('not-confirmed-human'); continue; }
      const d = r.decision;
      if (!d || !Number.isSafeInteger(d.decisionSeq) || d.decisionSeq < 0 || d.decisionSeq > r.flushSeq || d.matchId !== m.id) { exclude('unaligned'); continue; }
      const history = players.filter(p => p.kind === 'frame' && p.matchId === m.id && p.participantId === r.participantId && p.clientSeq <= d.decisionSeq);
      const watermark = history.find(p => p.clientSeq === d.decisionSeq && p.epoch === d.decisionEpoch);
      if (!watermark) { exclude('invalid-watermark'); continue; }
      const pub = history.findLast(p => p.message.t === 'm.public'), own = history.findLast(p => p.message.t === 'm.private');
      const observation = project(pub?.message, own?.message, r.playerId);
      if (!observation || !action) { exclude('missing-projection-or-action'); continue; }
      const seatRows = players.filter(p => p.matchId === m.id && p.seat === r.seat), pos = seatRows.indexOf(r);
      const annotations = latest.filter(a => a.matchId === m.id && a.seat === r.seat &&
        seatRows.findIndex(e => e.eventId === a.start) <= pos && seatRows.findIndex(e => e.eventId === a.end) >= pos);
      if (profile === 'human-reviewed' && !annotations.some(a => ['great', 'usable'].includes(a.quality))) { exclude('unreviewed'); continue; }
      samples.push({ eventId: r.eventId, matchId: m.id, seat: r.seat, participantId: r.participantId,
        splitGroup: `${id}:${m.roomCode}`, decisionSeq: d.decisionSeq, observationEventIds: [pub.eventId, own.eventId],
        observation, action, status: ack.status, verificationEventId: v.eventId,
        annotations: annotations.map(a => ({ annotationId: a.annotationId, revision: a.revision })),
        outcome: { complete: m.complete, status: m.status, teamVictory: m.victory,
          playerVictory: m.complete ? seatOutcome?.victory ?? null : null, alive: seatOutcome?.alive ?? null,
          eliminatedRound: seatOutcome?.eliminatedRound ?? null, roundsPassed: seatOutcome?.roundsPassed ?? null,
          bossDamage: seatOutcome?.stats?.bossDamage ?? null } });
    }
    const quality = { samples: samples.length, exclusions, actionCoverage: coverage,
      adapterCompatible: false, observationScope: 'collect-specific observed public/own-private projection',
      candidateShortlist: 'not computed: manual move/equip retain full parameters; adapter shortlist coverage unverified',
      missingObservation: ['unit detail/scouting', 'map geometry', 'adapter descriptors'],
      split: 'Group by room AND participant across collections; never split frames or seats of one match',
      outcomeCounts: { complete: collection.matches.filter(m => m.complete).length,
        teamVictories: collection.matches.filter(m => m.complete && m.victory).length,
        playerVictories: collection.matches.flatMap(m => m.complete ? m.outcome?.players?.filter(p => !p.isBot && p.victory === true) || [] : []).length,
        carriedEliminated: collection.matches.flatMap(m => m.complete && m.victory ? m.outcome?.players?.filter(p => !p.isBot && p.alive === false) || [] : []).length },
      matches: collection.matches.map(m => ({ id: m.id, status: m.status, complete: m.complete, teamVictory: m.victory,
        seats: m.outcome?.players?.map(p => ({ seat: p.seat, playerVictory: m.complete ? p.victory : null, alive: p.alive,
          eliminatedRound: p.eliminatedRound, bossDamage: p.stats?.bossDamage ?? null })) || [] })) };
    const bytes = Buffer.from(samples.map(s => JSON.stringify(s)).join('\n') + (samples.length ? '\n' : ''));
    const sources = Object.fromEntries(['players', 'verification', 'annotations'].map(channel => {
      const b = fs.readFileSync(this.file(id, channel + '.jsonl')); return [channel, { bytes: b.length, sha256: sha(b) }];
    }));
    const ref = { schema: 'arknights.human-dataset.v1', rules: RULES, projection: PROJECTION, capture: SCHEMA,
      collectionId: id, profile, replayMode: 'recorded-events', sha256: sha(bytes), sources, quality,
      annotationRevisions: revisions, files: { 'samples.jsonl': { bytes: bytes.length, sha256: sha(bytes) } } };
    const version = sha(JSON.stringify(ref));
    const dir = this.file(id, 'datasets/' + version); fs.mkdirSync(path.dirname(dir), { recursive: true });
    if (fs.existsSync(dir)) {
      if (sha(fs.readFileSync(path.join(dir, 'samples.jsonl'))) !== ref.sha256 || read(path.join(dir, 'dataset.json')).version !== version) throw Error('Dataset integrity failure');
    } else {
      fs.mkdirSync(dir, { mode: 0o700 }); fs.writeFileSync(path.join(dir, 'samples.jsonl'), bytes, { flag: 'wx', mode: 0o600 });
      atomic(path.join(dir, 'dataset.json'), { ...ref, version });
    }
    return { ...ref, version, path: dir };
  }
}
