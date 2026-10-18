import fs from 'node:fs';
import path from 'node:path';
import { randomUUID, randomBytes, timingSafeEqual } from 'node:crypto';
import { WebSocket, WebSocketServer } from 'ws';
import { Collections, sha } from './store.mjs';
import { capturedMatchClass } from './match.mjs';
import { redact } from './projection.mjs';
const WEB = new URL('./web/', import.meta.url);
const json = (res, value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)); };
const safeBody = async req => {
  let body = ''; for await (const chunk of req) { body += chunk; if (body.length > 65536) throw Error('Body too large'); }
  return JSON.parse(body || '{}');
};
export class Collector {
  constructor(root) {
    this.root = root; this.store = new Collections(root, { recover: true }); this.participants = new Map(); this.players = new Map();
    this.active = new Map(); this.matches = new Map(); this.pendingHandles = new Map(); this.connections = new Set();
    this.wss = new WebSocketServer({ noServer: true, maxPayload: 65536 });
  }
  matchClass(Match, Battle) { return capturedMatchClass(Match, Battle, this); }
  join(collectionId, consent) {
    this.store.get(collectionId);
    if (consent !== true) throw Error('Explicit consent required');
    const key = randomUUID(), p = { id: 'p-' + randomUUID(), collectionId, seq: 0 };
    this.participants.set(key, p);
    this.store.append(collectionId, 'players', { kind: 'consent', participantId: p.id, consentVersion: 2, scope: 'collection-batch' });
    return { key, participantId: p.id, collectionId };
  }
  takeRequest(playerId, message) {
    const meta = message._arkCollect;
    if (!meta || typeof meta.epoch !== 'string' || typeof meta.requestId !== 'string') return null;
    const key = `${meta.epoch}:${message.rid ?? 'none'}:${message.t}:${meta.requestId}`;
    const pending = this.pendingHandles.get(key); this.pendingHandles.delete(key);
    const { _arkCollect, ...clean } = message;
    if (!pending || pending.playerId !== playerId || pending.matchId !== this.active.get(playerId)?.captureId ||
        JSON.stringify(pending.message) !== JSON.stringify(clean)) return null;
    return pending;
  }
  organizer(id, req) {
    const value = req.headers['x-collect-organizer'];
    if (typeof value !== 'string' || value.length > 256) return false;
    try {
      const expected = JSON.parse(fs.readFileSync(this.store.file(id, 'access.json'), 'utf8')).organizerHash;
      return timingSafeEqual(Buffer.from(expected, 'hex'), Buffer.from(sha(value), 'hex'));
    } catch { return false; }
  }
  participant(id, req) {
    const p = this.participants.get(req.headers['x-collect-participant']);
    return p?.collectionId === id ? p : null;
  }
  authorizeSeat(id, matchId, seat, req) {
    const match = this.store.get(id).matches.find(m => m.id === matchId);
    if (!match || !match.seats.some(s => s.seat === seat)) throw Error('Unknown match/seat');
    if (this.organizer(id, req)) return match;
    const p = this.participant(id, req);
    if (!p || !match.seats.some(s => s.seat === seat && s.participantId === p.id)) {
      throw Object.assign(Error('Organizer or own-seat participant capability required'), { status: 403 });
    }
    return match;
  }
  requireOrganizer(id, req) {
    if (!this.organizer(id, req)) throw Object.assign(Error('Organizer capability required'), { status: 403 });
  }
  async http(req, res, pathname) {
    if (!(pathname === '/collect' || pathname.startsWith('/collect/') || pathname.startsWith('/api/collect'))) return false;
    try {
      if (pathname.startsWith('/api/collect')) {
        if (req.method === 'POST') {
          let foreign = req.headers['sec-fetch-site'] === 'cross-site';
          try { if (req.headers.origin) foreign ||= new URL(req.headers.origin).host !== req.headers.host; } catch { foreign = true; }
          if (foreign || !(req.headers['content-type'] || '').startsWith('application/json')) { json(res, { error: 'Same-origin JSON required' }, 403); return true; }
        }
        const parts = pathname.split('/').filter(Boolean), id = parts[2], action = parts[3];
        if (req.method === 'GET') {
          if (!id) json(res, this.store.list().filter(c => this.organizer(c.id, req)).map(c => ({ id: c.id, label: c.label, created: c.created, matches: c.matches })));
          else if (!action) {
            const c = this.store.get(id), organizer = this.organizer(id, req), p = this.participant(id, req);
            if (organizer) json(res, { ...c, organizer: true, annotations: this.store.rows(id, 'annotations') });
            else json(res, { id: c.id, label: c.label, created: c.created, schema: c.schema, rules: c.rules, organizer: false,
              matches: p ? c.matches.filter(m => m.seats.some(s => s.participantId === p.id)).map(m => ({
                id: m.id, matchNo: m.matchNo, roomCode: m.roomCode, status: m.status, complete: m.complete, victory: m.victory,
                replayMode: m.replayMode, seats: m.seats.filter(s => s.participantId === p.id) })) : [],
              annotations: p ? this.store.rows(id, 'annotations').filter(a => c.matches.find(m => m.id === a.matchId)?.seats.some(s => s.seat === a.seat && s.participantId === p.id)) : [] });
          }
          else if (action === 'timeline') {
            const url = new URL(req.url, 'http://local'), matchId = url.searchParams.get('match'), seat = Number(url.searchParams.get('seat'));
            this.authorizeSeat(id, matchId, seat, req);
            json(res, this.store.rows(id, 'players').filter(r => r.matchId === matchId && r.seat === seat));
          } else if (action === 'dataset') {
            this.requireOrganizer(id, req);
            const version = new URL(req.url, 'http://local').searchParams.get('version');
            if (!/^[a-f0-9]{64}$/.test(version)) throw Error('Invalid dataset version');
            const bytes = fs.readFileSync(this.store.file(id, `datasets/${version}/samples.jsonl`));
            res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Content-Disposition': 'attachment; filename=samples.jsonl' });
            res.end(bytes);
          } else if (action === 'battles') {
            const matchId = new URL(req.url, 'http://local').searchParams.get('match');
            const seat = Number(new URL(req.url, 'http://local').searchParams.get('seat'));
            this.authorizeSeat(id, matchId, seat, req);
            const p = this.participant(id, req), organizer = this.organizer(id, req);
            json(res, this.battles(id, matchId).filter(b => organizer || b.participants.includes(p.playerId)).map(({ frames, field, ...meta }) => meta));
          } else if (action === 'battle') {
            const name = new URL(req.url, 'http://local').searchParams.get('name');
            if (!/^[\w-]+$/.test(name)) throw Error('Invalid battle');
            const battle = JSON.parse(fs.readFileSync(this.store.file(id, `battles/${name}.json`)));
            const p = this.participant(id, req);
            if (!this.organizer(id, req) && (!p || !battle.participants.includes(p.playerId))) throw Object.assign(Error('Own-seat battle capability required'), { status: 403 });
            json(res, battle);
          } else json(res, { error: 'Not found' }, 404);
        } else if (req.method === 'POST') {
          const body = await safeBody(req);
          if (!id) {
            const collection = this.store.create(body.label), organizerKey = randomBytes(32).toString('hex');
            fs.writeFileSync(this.store.file(collection.id, 'access.json'), JSON.stringify({ organizerHash: sha(organizerKey) }), { flag: 'wx', mode: 0o600 });
            json(res, { ...collection, organizerKey }, 201);
          }
          else if (action === 'join') json(res, this.join(id, body.consent), 201);
          else if (action === 'annotations') {
            this.authorizeSeat(id, body.matchId, body.seat, req);
            const p = this.participant(id, req);
            json(res, this.store.annotate(id, { ...body, author: this.organizer(id, req) ? body.author : p.id }), 201);
          } else if (action === 'export') { this.requireOrganizer(id, req); json(res, this.store.export(id, body.profile)); }
          else json(res, { error: 'Not found' }, 404);
        } else json(res, { error: 'Method not allowed' }, 405);
        return true;
      }
      let file;
      if (pathname === '/collect' || pathname === '/collect/') file = new URL('index.html', WEB);
      else if (pathname === '/collect/app.mjs') file = new URL('app.mjs', WEB);
      else if (pathname === '/collect/style.css') file = new URL('style.css', WEB);
      else if (pathname === '/collect/replay.mjs') file = new URL('replay.mjs', WEB);
      else if (pathname === '/collect/replay') file = new URL('replay.html', WEB);
      else if (pathname === '/collect/probe.mjs') file = new URL('./probe.mjs', import.meta.url);
      if (!file) { res.writeHead(404); res.end(); return true; }
      res.writeHead(200, { 'Content-Type': pathname.endsWith('.mjs') ? 'text/javascript' : pathname.endsWith('.css') ? 'text/css' : 'text/html', 'Cache-Control': 'no-store' });
      res.end(fs.readFileSync(file)); return true;
    } catch (e) { json(res, { error: e.message }, e.status || 400); return true; }
  }
  battles(id, matchId) {
    const dir = this.store.file(id, 'battles');
    return fs.existsSync(dir) ? fs.readdirSync(dir).filter(f => f.startsWith(matchId + '-') && f.endsWith('.json')).map(f => JSON.parse(fs.readFileSync(path.join(dir, f)))) : [];
  }
  // Original module is served byte-for-byte plus a probe installer; frozen game is untouched.
  netModule() {
    return fs.readFileSync(path.join(this.root, 'game/public/js/net.js'), 'utf8') + `\nimport { installProbe } from '/collect/probe.mjs';
let credentials = null;
try { credentials = JSON.parse(sessionStorage.getItem('ark-collection')); } catch {}
if (credentials) installProbe(net, credentials);\n`;
  }
  upgrade(req, socket, head, upstream) {
    const url = new URL(req.url, 'http://local'); if (url.pathname !== '/ws') return false;
    try { if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) { socket.end('HTTP/1.1 403 Forbidden\r\n\r\n'); return true; } }
    catch { socket.end('HTTP/1.1 403 Forbidden\r\n\r\n'); return true; }
    let p = null;
    if (url.searchParams.has('collection')) {
      p = this.participants.get(url.searchParams.get('participant'));
      if (!p || p.collectionId !== url.searchParams.get('collection')) { socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return true; }
    }
    // Preserve the upstream admission policy; never send caller-controlled forwarding headers.
    if (upstream.network?.admission(req)) { socket.end('HTTP/1.1 429 Too Many Requests\r\n\r\n'); return true; }
    this.wss.handleUpgrade(req, socket, head, client => this.connect(client, p, upstream)); return true;
  }
  connect(client, p, upstream) {
    const backend = new WebSocket(`ws://127.0.0.1:${upstream.port}/ws`);
    const epoch = randomUUID(), pending = [], decisions = new Map(), outstanding = new Map(), sentAcks = new Map(), pendingKeys = new Map(), unconfirmed = new Set();
    let playerId = null, open = false, closed = false;
    this.connections.add(client); this.connections.add(backend);
    const match = () => this.active.get(playerId);
    const write = row => {
      const m = match(), seat = m?.players.get(playerId)?.seat;
      if (p) this.store.append(p.collectionId, 'players', { participantId: p.id, playerId, epoch,
        matchId: m?.captureId || null, seat: seat ?? null, ...row });
    };
    const reply = message => { if (client.readyState === 1) client.send(JSON.stringify(message)); };
    const processMessage = raw => {
      let msg; try { msg = JSON.parse(raw.toString()); } catch { backend.send(raw.toString()); return; }
      if (!msg || typeof msg !== 'object' || Array.isArray(msg) || typeof msg.t !== 'string') { backend.send(raw.toString()); return; }
      if (msg.t === 'collect.seen') {
        if (p && msg.epoch === epoch && Number.isSafeInteger(msg.seq) && msg.seq <= p.seq && msg.seq >= 0) {
          for (const [seq, ack] of sentAcks) if (seq <= msg.seq) {
            write({ kind: 'ack', ...ack, clientSeq: seq, receipt: 'probe-processed' });
            unconfirmed.delete(ack.requestId);
            sentAcks.delete(seq);
          }
        }
        return;
      }
      if (msg.t === 'collect.decision') {
        if (p && msg.decision && typeof msg.action === 'string') {
          const d = msg.decision;
          decisions.set(`${msg.rid ?? 'none'}:${msg.action}`, Object.fromEntries(['decisionId', 'decisionSeq', 'decisionEpoch', 'matchId', 'invokedAt'].map(k => [k, d[k]])));
          if (decisions.size > 512) decisions.delete(decisions.keys().next().value);
        }
        return;
      }
      const room = upstream.lobby?.rooms.get(upstream.registry?.byId(playerId)?.roomCode);
      // Room lookup does not depend on a Net/Session semantic change.
      const actualRoom = room || [...(upstream.lobby?.rooms.values() || [])].find(r => r.seats.some(s => s?.playerId === playerId));
      const capturedRoom = p || actualRoom?.seats.some(s => this.players.has(s?.playerId));
      if (capturedRoom && ((msg.t === 'room.setPlugins' && msg.plugins?.length) || msg.t === 'room.setMode' ||
        (msg.t === 'room.start' && (actualRoom?.modded || actualRoom?.content?.execution === 'script')))) {
        reply({ t: 'error', code: 'BAD_MSG', rid: msg.rid, detail: 'Collect V1 supports vanilla server-combat only' }); return;
      }
      if (msg.t === 'room.start' && capturedRoom) {
        const humans = actualRoom?.seats.filter(s => s && !s.isBot) || [];
        const owners = humans.map(s => this.players.get(s.playerId));
        if (!humans.length || owners.some(o => !o || o.collectionId !== (p?.collectionId || owners.find(Boolean)?.collectionId))) {
          reply({ t: 'error', code: 'BAD_MSG', rid: msg.rid, detail: 'Every human must explicitly join the same collection before start' }); return;
        }
      }
      delete msg._arkCollect;
      const relevant = /^(g\.|b\.)/.test(msg.t) || msg.t === 'room.loadout';
      if (p && relevant) {
        const requestId = randomUUID(), key = `${msg.rid ?? 'none'}:${msg.t}`, decision = decisions.get(key) || null;
        decisions.delete(key);
        write({ kind: 'request', requestId, message: msg, decision, flushSeq: p.seq, source: 'human' });
        if (msg.t !== 'room.loadout') {
          const hkey = `${epoch}:${msg.rid ?? 'none'}:${msg.t}:${requestId}`;
          this.pendingHandles.set(hkey, { requestId, epoch, playerId, matchId: match()?.captureId || null, message: structuredClone(msg) });
          pendingKeys.set(requestId, hkey);
          msg._arkCollect = { epoch, requestId };
        }
        if (msg.rid != null) { const queue = outstanding.get(msg.rid) || []; queue.push(requestId); outstanding.set(msg.rid, queue); unconfirmed.add(requestId); }
      }
      if (pendingKeys.size > 512 || unconfirmed.size > 512) { client.close(1008, 'Capture acknowledgement backlog'); return; }
      backend.send(JSON.stringify(msg));
    };
    backend.on('open', () => { open = true; for (const raw of pending.splice(0)) processMessage(raw); });
    client.on('message', raw => { if (open) processMessage(raw); else if (pending.length < 64) pending.push(raw); else client.close(1009); });
    backend.on('message', raw => {
      let msg; try { msg = JSON.parse(raw.toString()); } catch { reply({ t: 'error', code: 'INTERNAL' }); return; }
      if (msg.t === 'welcome') {
        playerId = msg.playerId;
        if (p) {
          if (p.playerId && p.playerId !== playerId) { client.close(1008, 'Participant is bound to its original identity'); backend.close(); return; }
          p.playerId = playerId; this.players.set(playerId, p); write({ kind: 'connected' });
        }
        else if (this.players.has(playerId)) {
          // A consented identity resumed outside the instrumented page: never silently certify its decisions.
          const prior = this.players.get(playerId), active = this.active.get(playerId);
          if (active && this.matches.get(active.captureId) === active && !active.ended && !active.disposed) {
            this.store.fail(prior.collectionId, 'active identity resumed without capture probe');
          } else {
            this.players.delete(playerId); this.active.delete(playerId);
          }
        }
      }
      if (p) {
        const seq = ++p.seq;
        if (msg.rid != null && outstanding.has(msg.rid) && ['ok', 'error'].includes(msg.t)) {
          const queue = outstanding.get(msg.rid), requestId = queue.shift(); if (!queue.length) outstanding.delete(msg.rid);
          this.pendingHandles.delete(pendingKeys.get(requestId)); pendingKeys.delete(requestId);
          const ack = { requestId, status: msg.t === 'ok' ? 'accepted' : 'rejected', message: msg };
          write({ kind: 'ack-sent', ...ack, clientSeq: seq }); sentAcks.set(seq, ack);
        }
        // Raw messages are restricted, never served as a dataset or mixed into verification observations.
        write({ kind: 'frame', clientSeq: seq, message: redact(msg) });
        msg._collect = { seq, epoch, matchId: match()?.captureId || null };
      }
      reply(msg);
    });
    const close = () => {
      if (closed) return; closed = true;
      write({ kind: 'disconnected', pendingRequests: [...unconfirmed] });
      for (const requestId of unconfirmed) write({ kind: 'ack', requestId, status: 'unknown' });
      outstanding.clear(); unconfirmed.clear();
      for (const key of pendingKeys.values()) this.pendingHandles.delete(key); pendingKeys.clear();
      client.close(); backend.close(); this.connections.delete(client); this.connections.delete(backend);
    };
    client.once('close', close); backend.once('close', close);
    client.on('error', () => backend.close()); backend.on('error', () => client.close(1011));
  }
  close() { for (const s of this.connections) s.terminate(); this.wss.close(); }
}
