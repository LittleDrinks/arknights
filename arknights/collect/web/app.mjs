const $ = id => document.getElementById(id);
const headers = route => {
  const collectionId = route.split(/[/?]/)[1];
  const organizer = localStorage.getItem('ark-organizer:' + collectionId);
  let participant; try { participant = JSON.parse(sessionStorage.getItem('ark-collection')); } catch {}
  return { ...(organizer ? { 'X-Collect-Organizer': organizer } : {}),
    ...(participant?.collectionId === collectionId ? { 'X-Collect-Participant': participant.key } : {}) };
};
const api = async (url, body) => { const r = await fetch('/api/collect' + url, {
  method: body === undefined ? 'GET' : 'POST', headers: { ...headers(url), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await r.json(); if (!r.ok) throw Error(value.error); return value; };
const run = fn => async e => { e?.preventDefault(); $('error').textContent = ''; try { await fn(e); } catch (err) { $('error').textContent = err.message; } };
const el = (tag, text) => { const node = document.createElement(tag); if (text) node.textContent = text; return node; };
const id = new URLSearchParams(location.search).get('id'); let current, selected, annotationId = null;
$('create-button').onclick = run(async () => { const c = await api('', { label: $('label').value }); localStorage.setItem('ark-organizer:' + c.id, c.organizerKey); location.href = '/collect?id=' + c.id; });
$('join').onclick = run(async () => {
  if (!$('consent').checked) throw Error('请先阅读并勾选记录同意');
  const p = await api('/' + id + '/join', { consent: true });
  sessionStorage.setItem('ark-collection', JSON.stringify(p));
  location.href = '/?collect=' + id;
});
$('refresh').onclick = run(load);
async function timeline(match, seat) {
  selected = { matchId: match.id, seat }; annotationId = null;
  const rows = await api(`/${id}/timeline?match=${match.id}&seat=${seat}`);
  $('timeline').replaceChildren(el('h3', `席 ${seat + 1} · ${match.status} · ${rows.length} 条记录`));
  const replay = el('a', '原生页面回看本席 / 战斗'); replay.href = `/collect/replay?id=${id}&match=${match.id}&seat=${seat}`; $('timeline').append(replay);
  const names = { 'g.buy':'购买', 'g.move':'布阵 / 调整朝向', 'g.equip':'配发装备', 'g.reward':'选择奖励', 'g.choice':'机变选择',
    'g.band':'选择策略', 'g.ready':'准备战斗', 'g.sell':'出售', 'g.refresh':'刷新商店', 'g.levelUp':'升级商店', 'g.infoReady':'确认本局信息',
    'g.freeze':'冻结商店', 'g.autoplay':'切换托管', 'g.watch':'查看战场', 'g.unitStats':'查看单位详情', 'g.leave':'退出模拟' };
  const table = el('table'); table.append(el('tr')); for (const h of ['回合 / 阶段', '操作', '结果']) table.firstChild.append(el('th', h));
  const details = el('details'), summary = el('summary', '数据质量与水位详情'), technical = el('pre'); details.append(summary, technical);
  let round = 0, phase = '', previousPhase = ''; const debug = [];
  $('start').replaceChildren(); $('end').replaceChildren();
  const acks = new Map(rows.filter(r => r.kind === 'ack').map(r => [r.requestId, r.status]));
  const status = { accepted:'已确认', rejected:'已拒绝', unknown:'回执未知' };
  for (const [i, r] of rows.entries()) {
    if (r.message?.t === 'm.public') { round = r.message.round; phase = r.message.phase; }
    const useful = r.kind === 'request' || r.message?.t === 'm.result' ||
      (r.message?.t === 'm.public' && phase !== previousPhase) || ['connected','disconnected'].includes(r.kind);
    if (r.message?.t === 'm.public') previousPhase = phase;
    if (useful) {
      const text = names[r.message?.t] || (r.message?.t === 'm.result' ? '实际结算' : r.kind === 'disconnected' ? '断开连接' : r.kind === 'connected' ? '连接恢复' : phase);
      const row = el('tr'); row.append(el('td', `R${round} · ${phase}`), el('td', text), el('td', status[acks.get(r.requestId)] || '')); table.append(row);
      for (const field of ['start', 'end']) $(field).add(new Option(`R${round} ${phase} · ${text}`, r.eventId));
    }
    if (r.kind === 'request') debug.push({ event:i, action:r.message.t, decisionSeq:r.decision?.decisionSeq ?? null, flushSeq:r.flushSeq, epoch:r.epoch, receipt:acks.get(r.requestId) || 'unknown' });
  }
  technical.textContent = JSON.stringify(debug, null, 2); $('timeline').append(details);
  $('end').selectedIndex = $('end').options.length - 1; $('timeline').append(table); $('annotation').hidden = false; revisions();
}
function revisions() {
  $('revisions').replaceChildren(el('h3', '标注修订（追加保存）'));
  for (const a of current.annotations.filter(a => a.matchId === selected?.matchId && a.seat === selected?.seat)) {
    const button = el('button', `${a.annotationId.slice(0, 8)} v${a.revision} · ${a.goalCore} · ${a.quality} · ${a.note}`);
    button.onclick = () => { annotationId = a.annotationId; for (const [key, value] of Object.entries({ start:a.start, end:a.end, core:a.goalCore, support:a.goalSupport, stage:a.stage, quality:a.quality, note:a.note, author:a.author })) $(key).value = value; };
    $('revisions').append(button);
  }
}
$('new-annotation').onclick = () => { annotationId = null; $('note').value = ''; };
$('annotation').onsubmit = run(async () => {
  await api(`/${id}/annotations`, { ...selected, annotationId, start:$('start').value, end:$('end').value,
    goalCore:$('core').value, goalSupport:$('support').value, stage:$('stage').value, quality:$('quality').value, note:$('note').value, author:$('author').value });
  current = await api('/' + id); revisions(); $('feedback').textContent = '保存成功：已追加标注修订，原记录保持不变。';
});
$('export').onclick = run(async () => {
  const ref = await api(`/${id}/export`, { profile:$('profile').value }); $('dataset').textContent = JSON.stringify(ref, null, 2);
  $('dataset-summary').textContent = `已导出 ${ref.quality.samples} 条已确认示范；${ref.quality.outcomeCounts.complete} 场完整局。当前为 collect 专用投影，候选覆盖尚未验证。`;
  const route = `/${id}/dataset?version=${ref.version}`;
  const response = await fetch('/api/collect' + route, { headers: headers(route) });
  if (!response.ok) throw Error('样本下载失败');
  const sampleURL = URL.createObjectURL(await response.blob()), a = el('a', '下载示范样本'); a.href = sampleURL; a.download = 'samples.jsonl'; $('dataset-summary').append(' ', a);
  const blob = new Blob([JSON.stringify(ref, null, 2)], { type:'application/json' }), url = URL.createObjectURL(blob), download = el('a');
  download.href=url; download.download='DatasetRef.json'; download.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
async function load() {
  if (!id) {
    $('list').replaceChildren();
    for (const key of Object.keys(localStorage).filter(k => k.startsWith('ark-organizer:'))) {
      const c = await api('/' + key.slice('ark-organizer:'.length)); const a = el('a', `${c.label} · ${c.matches.length} 场 · ${c.created}`); a.href='/collect?id='+c.id; const card=el('div'); card.className='card'; card.append(a); $('list').append(card); } return;
  }
  current = await api('/' + id); $('detail').hidden=false; $('create').hidden=true; $('heading').textContent=current.label; $('export-controls').hidden=!current.organizer;
  $('invite').textContent='邀请链接：'+location.href+'（每位玩家单独同意，再在游戏内加入同一房间）'; $('matches').replaceChildren();
  for (const m of current.matches) {
    const card=el('div'); card.className='card'; card.append(el('p', `${m.roomCode} #${m.matchNo} · ${m.status} · ${m.complete ? m.victory ? '团队实际胜利' : '团队实际失败' : '未认证完整局'} · ${m.replayMode}`));
    for(const p of m.seats) { const b=el('button', `席 ${p.seat+1}${p.isBot?' · AI':''}`); b.disabled=p.isBot; b.onclick=run(()=>timeline(m,p.seat)); card.append(b); } $('matches').append(card);
  }
}
await run(load)();
