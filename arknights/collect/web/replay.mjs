// Recorded facts projected into the ORIGINAL game screen/renderer. No re-execution or policy inference.
import '/js/ui/compat.js';
import { render } from '/vendor/preact.module.js';
import { html, UiHosts } from '/js/ui/components.js';
import { GameScreen } from '/js/screens/game.js';
import { ToastHost } from '/js/ui/toasts.js';
import { GuideHost } from '/js/ui/guide.js';
import { data } from '/js/data.js';
import { GAME_FILES } from '/js/ui/gameComponents.js';
import { store, emptyMatch } from '/js/store.js';
import { net, NetError } from '/js/net.js';
import { initLang } from '/js/ui/lang.js';
import { settingsStore } from '/js/ui/settings.js';
import { installDeviceSupport } from '/js/ui/device.js';
const $=id=>document.getElementById(id), query=new URLSearchParams(location.search), id=query.get('id'), matchId=query.get('match'), seat=Number(query.get('seat'));
const headers = () => {
  const organizer = localStorage.getItem('ark-organizer:' + id); let participant;
  try { participant = JSON.parse(localStorage.getItem('ark-participant:' + id) || sessionStorage.getItem('ark-collection')); } catch {}
  return { ...(organizer ? { 'X-Collect-Organizer':organizer } : {}), ...(participant?.collectionId === id ? { 'X-Collect-Participant':participant.key } : {}) };
};
const json=async path=>{const r=await fetch(`/api/collect/${id}/${path}`, { headers: headers() });if(!r.ok)throw Error(await r.text());return r.json();};
const clone=x=>structuredClone(x); let rows, pub, own, recording=null, index=0, playing=false, elapsed=0, lastAt=0, frameIndex=0;
const state={ready:false,replayMode:'recorded-events',source:'human-browser',seat,errors:[]}; globalThis.__collectReplay=state;
function display(i) {
  playing=false; recording=null; index=i; pub=null;own=null;
  for(const r of rows.slice(0,i+1)) { if(r.message?.t==='m.public')pub=r.message; if(r.message?.t==='m.private')own=r.message; }
  if(!pub||!own)return;
  const publicView=clone(pub);publicView.serverNow=Date.now();publicView.deadline=null;publicView.paused=false;
  for(const p of publicView.players||[])p.autoplay=false;
  store.set({me:{playerId:own.playerId,name:`采集席 ${seat+1}`,token:null},match:{...emptyMatch(),public:publicView,private:clone(own)},ticker:[],emotes:[]});
  $('status').textContent=`R${pub.round} · ${pub.phase} · 事件 ${i+1}/${rows.length}`;
}
function field() {
  const p=clone(pub);p.phase=recording.kind==='boss'?'FINAL_ASSAULT':recording.kind==='hidden'?'HIDDEN_CORE':recording.kind==='unite'?'UNITE':'COMBAT';p.round=recording.round;p.deadline=null;p.serverNow=Date.now();p.combatMode='server';
  p.fields=[{fieldId:recording.field.fieldId,kind:recording.kind,players:recording.participants,live:true}];
  for(const ps of p.players||[])if(ps.playerId===own.playerId)ps.fieldId=recording.field.fieldId;
  store.patch('match',{public:p,private:clone(own),field:clone(recording.field),result:null,battle:null});
  net._emit('m.field',{t:'m.field',...clone(recording.field)});
}
function push(frame) {
  const fieldId=recording.field.fieldId;
  if(frame.ev.length)net._emit('b.ev',{t:'b.ev',fieldId,gt:frame.snap.t,ev:frame.ev});
  net._emit('b.snap',{...frame.snap,t:'b.snap',fieldId,gt:frame.snap.t});
}
function seek(n) {
  playing=false;field();frameIndex=n;elapsed=recording.frames[n].snap.t;
  const spawns=new Map(recording.field.units.map(u=>[u.id,u]));
  for(const f of recording.frames.slice(0,n+1))for(const ev of f.ev)if(ev[0]==='spawn')spawns.set(ev[1].id,ev[1]);
  const snap=recording.frames[n].snap, ids=new Set(snap.units.map(u=>u[0]));
  requestAnimationFrame(()=>push({snap,ev:[...spawns.values()].filter(u=>ids.has(u.id)).map(u=>['spawn',u])}));
}
async function loadBattle(name) {
  const rec=await json(`battle?name=${name}`);
  let observedRound=null;
  const observationIndex=rows.findIndex(r=>{if(r.message?.t==='m.public')observedRound=r.message.round;return observedRound===rec.round&&r.message?.t==='m.private';});
  const fallback=rows.findIndex(r=>r.message?.t==='m.public'&&r.message.round===rec.round);
  if(observationIndex<0&&fallback<0)throw Error('该回合缺少本席已记录观测');
  display(observationIndex>=0?observationIndex:fallback);
  recording=rec;state.battle=recording.name;state.battleReady=false;field();frameIndex=0;elapsed=0;
  // Mount GameScreen and its renderer subscriptions before emitting the initial frame.
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  push(recording.frames[0]);frameIndex=1;playing=false;$('scrub').max=recording.frames.length-1;$('scrub').value=0;state.battleReady=true;$('ready').textContent='战斗录像已就绪';$('ready').dataset.state='ready';
}
function tick(at){const dt=Math.min(.15,(at-(lastAt||at))/1000);lastAt=at;
  if(playing&&recording){elapsed+=dt*2;while(frameIndex<recording.frames.length&&recording.frames[frameIndex].snap.t<=elapsed)push(recording.frames[frameIndex++]);
    if(frameIndex===recording.frames.length)playing=false;$('scrub').value=Math.max(0,frameIndex-1);$('status').textContent=`R${recording.round} · ${elapsed.toFixed(1)} / ${recording.duration.toFixed(1)}s`;}
  $('play').textContent=playing?'暂停':'播放战斗'; requestAnimationFrame(tick);
}
try {
  installDeviceSupport();await initLang();await data.loadAll(...GAME_FILES,'assets','local');settingsStore.set({...settingsStore.get(),quality:'medium',officialFx:'off'});
  net.request=()=>Promise.reject(new NetError('REPLAY_READ_ONLY','回放中无法操作'));net.connect=()=>{};net.status='online';
  rows=await json(`timeline?match=${matchId}&seat=${seat}`);let havePublic=false,havePrivate=false; const first=rows.findIndex(r=>{havePublic ||= r.message?.t==='m.public';havePrivate ||= r.message?.t==='m.private';return havePublic&&havePrivate;});
  if(first<0)throw Error('No recorded private view for this seat');
  store.set({connection:{status:'online',ping:0,everOnline:true},session:{entered:true},room:{code:'COLLECT',inMatch:true,mode:'coop',spectators:[],seats:[]},ui:{pendingJoin:null,restoring:false,buildStale:false}});
  let phase=null;const firstField=rows.findIndex(r=>{if(r.message?.t==='m.public')phase=r.message.phase;
    return r.message?.t==='m.private'&&phase&&!['LOBBY','INFO_CHECK','BAND_DRAFT'].includes(phase);});
  display(firstField>=0?firstField:first);
  render(html`<div class="app-root"><${GameScreen}/><${UiHosts}/><${ToastHost}/><${GuideHost}/></div>`,document.getElementById('app'));
  for(const [i,r]of rows.entries())if(r.kind==='request'||r.message?.t==='m.private')$('events').add(new Option(`${i} ${r.message?.t||r.kind}`,i));
  $('events').onchange=()=>display(Number($('events').value));
  for(const b of (await json(`battles?match=${matchId}&seat=${seat}`)).filter(b=>b.participants.includes(own.playerId)))$('battles').add(new Option(`R${b.round} ${b.kind} ${b.participants.join(',')}`,b.name));
  $('battles').onchange=()=>loadBattle($('battles').value).catch(e=>{$('status').textContent=e.message;$('ready').textContent='录像载入失败';$('ready').dataset.state='error';state.errors.push(e.message);});
  $('play').onclick=()=>{playing=!playing;};$('scrub').oninput=()=>recording&&seek(Number($('scrub').value));$('back').href=`/collect?id=${id}`;
  state.ready=true;$('ready').textContent='记录已就绪';$('ready').dataset.state='ready';requestAnimationFrame(tick);
} catch(e){state.errors.push(e.message);$('ready').textContent='记录载入失败';$('ready').dataset.state='error';$('status').textContent=e.message;console.error(e);}
