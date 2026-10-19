const $ = id => document.getElementById(id);
const node = (tag, text) => { const e = document.createElement(tag); e.textContent = text; return e; };
function headers(id) {
  let p; try { p = JSON.parse(localStorage.getItem('ark-participant:' + id)); } catch {}
  const key = localStorage.getItem('ark-organizer:' + id);
  return { ...(p ? { 'X-Collect-Participant': p.key } : {}), ...(key ? { 'X-Collect-Organizer': key } : {}) };
}
async function api(id, suffix = '', body) {
  const r = await fetch('/api/collect/' + id + suffix, { method: body === undefined ? 'GET' : 'POST',
    headers: { ...headers(id), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await r.json(); if (!r.ok) throw Error(value.error); return value;
}
const run = fn => async e => { e?.preventDefault(); $('error').textContent = ''; try { await fn(e); } catch (e) { $('error').textContent = e.message; } };
let selection;
async function show(id, match, seat) {
  selection = { id, matchId: match.id, seat };
  const rows = await api(id, `/timeline?match=${match.id}&seat=${seat}`);
  $('detail').hidden = false; $('heading').textContent = `${match.roomCode} · 第${match.matchNo}局 · 席${seat + 1} · ${match.status}`;
  $('replay').href = `/collect/replay?id=${id}&match=${match.id}&seat=${seat}`;
  $('start').replaceChildren(); $('end').replaceChildren();
  const table = node('table', ''), head = node('tr', '');
  for (const text of ['回合/阶段', '操作', '结果']) head.append(node('th', text));
  table.append(head); let round = '', phase = '';
  for (const row of rows) {
    if (row.message?.t === 'm.public') { round = row.message.round; phase = row.message.phase; }
    if (row.kind !== 'request') continue;
    const ack = rows.findLast(r => r.kind === 'ack' && r.requestId === row.requestId);
    const tr = node('tr', '');
    for (const text of [`R${round} ${phase}`, JSON.stringify(row.message), ack?.status || 'unknown']) tr.append(node('td', text));
    table.append(tr);
    const label = `R${round} ${phase} · ${row.message.t}`;
    $('start').add(new Option(label, row.eventId)); $('end').add(new Option(label, row.eventId));
  }
  $('end').selectedIndex = $('end').options.length - 1;
  $('annotation').hidden = !$('start').options.length;
  $('timeline').replaceChildren(node('p', `本席保存 ${rows.length} 条事件（含状态、请求与回执）`), table);
  const c = await api(id);
  $('revisions').replaceChildren(...(c.annotations || []).filter(a => a.matchId === match.id && a.seat === seat)
    .map(a => node('p', `${a.stage} · ${a.quality} · ${a.goalCore} ${a.goalSupport} · ${a.note}`)));
}
async function load() {
  const ids = [...new Set(Object.keys(localStorage).filter(k => /^(ark-participant:|ark-organizer:)/.test(k)).map(k => k.slice(k.indexOf(':') + 1)))];
  $('rooms').replaceChildren();
  for (const id of ids) {
    const c = await api(id), section = node('section', '');
    section.append(node('h2', c.label), node('p', `${c.created} · ${c.matches.length} 局`));
    for (const m of c.matches) {
      const p = node('p', `第${m.matchNo}局 · ${m.status} · ${m.victory === true ? '团队胜利' : m.victory === false ? '团队失败' : '胜负未定'} `);
      for (const s of m.seats.filter(s => !s.isBot)) {
        const b = node('button', `查看席${s.seat + 1}`); b.onclick = run(() => show(id, m, s.seat)); p.append(b);
      }
      section.append(p);
    }
    if (c.organizer) {
      const b = node('button', '导出全部已确认人工操作'); b.onclick = run(async () => {
        const ref = await api(id, '/export', { profile: 'all-human' });
        const response = await fetch(`/api/collect/${id}/dataset?version=${ref.version}`, { headers: headers(id) });
        if (!response.ok) throw Error(await response.text());
        const url = URL.createObjectURL(await response.blob()), link = node('a', '');
        link.href = url; link.download = id + '-samples.jsonl'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }); section.append(b);
    }
    $('rooms').append(section);
  }
  if (!ids.length) $('rooms').append(node('p', '此浏览器尚未参与采集房间。返回游戏正常建房或加入朋友的房间即可。'));
}
$('annotation').onsubmit = run(async () => {
  const s = selection;
  await api(s.id, '/annotations', { matchId: s.matchId, seat: s.seat, start: $('start').value, end: $('end').value,
    goalCore: $('core').value, goalSupport: $('support').value, stage: $('stage').value, quality: $('quality').value, note: $('note').value });
  $('feedback').textContent = '标注已保存';
});
$('refresh').onclick = run(load);
await run(load)();
