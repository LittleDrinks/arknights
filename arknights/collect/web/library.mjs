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
const replayUrl = (id, matchId, seat) => '/collect/replay?' + new URLSearchParams({ id, match: matchId, seat });
const statusText = m => m.victory === true ? '团队胜利' : m.victory === false ? '团队失败'
  : ({ recording: '记录中', partial: '已中断', 'technical-failure': '采集异常' }[m.status] || '胜负未定');
const actionText = m => ({ 'g.buy': '购买', 'g.sell': '出售', 'g.refresh': '刷新商店', 'g.move': '调整布阵',
  'g.equip': '装备', 'g.levelUp': '升级', 'g.ready': '准备就绪', 'g.infoReady': '确认情报',
  'g.band': '选择盟约', 'g.choice': '选择机变', 'g.watch': '切换战场', 'g.reward': '选择奖励',
  'g.freeze': '冻结商店' }[m.t] || m.t);
async function show(id, match, seat) {
  selection = { id, matchId: match.id, seat };
  const rows = await api(id, `/timeline?match=${match.id}&seat=${seat}`);
  $('detail').hidden = false; $('heading').textContent = `${match.roomCode} · 第${match.matchNo}局 · 席位 ${seat + 1}`;
  $('replay').href = replayUrl(id, match.id, seat);
  $('start').replaceChildren(); $('end').replaceChildren();
  const table = node('table', ''), head = node('tr', '');
  for (const text of ['回合/阶段', '操作', '结果']) head.append(node('th', text));
  table.append(head); let round = '', phase = '';
  for (const row of rows) {
    if (row.message?.t === 'm.public') { round = row.message.round; phase = row.message.phase; }
    if (row.kind !== 'request') continue;
    const ack = rows.findLast(r => r.kind === 'ack' && r.requestId === row.requestId);
    const tr = node('tr', '');
    for (const text of [`R${round} ${phase}`, actionText(row.message),
      ({ accepted: '已确认', rejected: '已拒绝' }[ack?.status] || '未确认')]) tr.append(node('td', text));
    tr.children[1].title = JSON.stringify(row.message);
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
  $('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
async function load() {
  const ids = [...new Set(Object.keys(localStorage).filter(k => /^(ark-participant:|ark-organizer:)/.test(k)).map(k => k.slice(k.indexOf(':') + 1)))];
  $('rooms').replaceChildren(); let matches = 0;
  for (const id of ids) {
    const c = await api(id), section = node('section', '');
    const meta = node('p', `${new Date(c.created).toLocaleString()} · ${c.matches.length} 局`); meta.className = 'room-meta';
    section.append(node('h2', c.label), meta); matches += c.matches.length;
    for (const m of c.matches) {
      const block = node('div', ''); block.className = 'match';
      const head = node('div', ''); head.className = 'match-head';
      const badge = node('span', statusText(m)); badge.className = 'badge' + (m.victory === true ? ' win' : m.victory === false ? ' loss' : '');
      head.append(node('strong', `第 ${m.matchNo} 局`), badge); block.append(head);
      const seats = node('div', ''); seats.className = 'seats';
      for (const s of m.seats.filter(s => !s.isBot)) {
        const seat = node('div', ''); seat.className = 'seat';
        const label = node('div', ''), title = node('span', `席位 ${s.seat + 1}`); title.className = 'seat-label';
        label.append(title, node('small', s.name || '真人玩家'));
        const actions = node('div', ''); actions.className = 'seat-actions';
        const link = node('a', '▶ 原生回放'); link.className = 'primary'; link.href = replayUrl(id, m.id, s.seat);
        const b = node('button', '明细 / 标注'); b.onclick = run(() => show(id, m, s.seat));
        actions.append(link, b); seat.append(label, actions); seats.append(seat);
      }
      block.append(seats); section.append(block);
    }
    if (c.organizer) {
      const b = node('button', '导出全部已确认人工操作'); b.onclick = run(async () => {
        const ref = await api(id, '/export', { profile: 'all-human' });
        const response = await fetch(`/api/collect/${id}/dataset?version=${ref.version}`, { headers: headers(id) });
        if (!response.ok) throw Error(await response.text());
        const url = URL.createObjectURL(await response.blob()), link = node('a', '');
        link.href = url; link.download = id + '-samples.jsonl'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }); b.className = 'export'; section.append(b);
    }
    $('rooms').append(section);
  }
  if (!ids.length) $('rooms').append(node('p', '此浏览器尚未参与采集房间。返回游戏正常建房或加入朋友的房间即可。'));
  $('count').textContent = `${ids.length} 个房间 · ${matches} 局记录`;
}
$('annotation').onsubmit = run(async () => {
  const s = selection;
  await api(s.id, '/annotations', { matchId: s.matchId, seat: s.seat, start: $('start').value, end: $('end').value,
    goalCore: $('core').value, goalSupport: $('support').value, stage: $('stage').value, quality: $('quality').value, note: $('note').value });
  $('feedback').textContent = '标注已保存';
});
$('refresh').onclick = run(load);
await run(load)();
