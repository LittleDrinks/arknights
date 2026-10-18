// Installed on the original Net instance before main.js boots. Never changes Session/Net semantics.
export function installProbe(net, credentials = null) {
  if (net.__collectProbe) return net.__collectProbe;
  const state = { seq: 0, epoch: null, matchId: null, decisions: new Map(), active: null };
  net.__collectProbe = state;
  if (credentials) {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    net.url = `${scheme}//${location.host}/ws?collection=${encodeURIComponent(credentials.collectionId)}&participant=${encodeURIComponent(credentials.key)}`;
  }
  const emit = net._emit;
  net._emit = function(type, payload) {
    if (state.receiving && type === state.receiving.t) {
      const meta = state.receiving._collect;
      if (meta) Object.assign(state, { seq: meta.seq, epoch: meta.epoch, matchId: meta.matchId });
    }
    return emit.call(this, type, payload);
  };
  const receive = net._onMessage;
  net._onMessage = function(data) {
    try { state.receiving = JSON.parse(data); } catch { state.receiving = null; }
    const receiving = state.receiving;
    const result = receive.call(this, data);
    state.receiving = null;
    try { const m = JSON.parse(data); if (m._collect) Object.assign(state, {
      seq: m._collect.seq, epoch: m._collect.epoch, matchId: m._collect.matchId }); } catch { /* non-JSON */ }
    if (receiving?._collect && this.ws?.readyState === 1) {
      this.ws.send(JSON.stringify({ t: 'collect.seen', seq: receiving._collect.seq, epoch: receiving._collect.epoch }));
    }
    return result;
  };
  const next = net._nextRid;
  net._nextRid = function() { const rid = next.call(this); if (state.active) state.decisions.set(rid, state.active); if (state.decisions.size > 512) state.decisions.delete(state.decisions.keys().next().value); return rid; };
  const stamp = () => ({ decisionId: globalThis.crypto.randomUUID(), decisionSeq: state.seq,
    decisionEpoch: state.epoch, matchId: state.matchId, invokedAt: Date.now() });
  const request = net.request;
  net.request = function(t, fields, opts) {
    state.active = stamp();
    try { return request.call(this, t, fields, opts); } finally { state.active = null; }
  };
  const send = net.send;
  net.send = function(t, fields) {
    state.active = stamp(); try { return send.call(this, t, fields); } finally { state.active = null; }
  };
  const raw = net._sendRaw;
  net._sendRaw = function(message) {
    const decision = state.decisions.get(message.rid) || state.active;
    if (decision && this.ws?.readyState === 1 && (message.t.startsWith('g.') || message.t.startsWith('b.') || message.t === 'room.loadout')) {
      this.ws.send(JSON.stringify({ t: 'collect.decision', rid: message.rid, action: message.t, decision }));
      state.decisions.delete(message.rid);
    }
    return raw.call(this, message);
  };
  return state;
}
