import test from 'node:test';
import assert from 'node:assert/strict';
import { WebSocket } from 'ws';
const base=process.env.ARK_COLLECT_URL;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const organizers=new Map();
async function api(route,body,{organizer=true,participant=null,key=null}={}) {
  const id=route.split(/[/?]/)[1],cap=key||(organizer?organizers.get(id):null);
  const r=await fetch(base+'/api/collect'+route,{method:body===undefined?'GET':'POST',headers:{...(cap?{'X-Collect-Organizer':cap}:{}),...(participant?{'X-Collect-Participant':participant.key}:{}),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const value=await r.json();if(value.organizerKey)organizers.set(value.id,value.organizerKey);return {status:r.status,value};
}
async function client(t,participant=null,token=null) {
  const u=new URL(base);u.protocol=u.protocol==='https:'?'wss:':'ws:';u.pathname='/ws';
  if(participant){u.searchParams.set('collection',participant.collectionId);u.searchParams.set('participant',participant.key);}
  const ws=new WebSocket(u),messages=[];let rid=0;
  ws.on('message',s=>{const m=JSON.parse(s);messages.push(m);if(participant&&m._collect)ws.send(JSON.stringify({t:'collect.seen',seq:m._collect.seq,epoch:m._collect.epoch}));});await new Promise((r,j)=>{ws.once('open',r);ws.once('error',j);});
  t.after(()=>ws.terminate());
  const wait=async fn=>{for(let i=0;i<200;i++){const found=messages.findLast(fn);if(found)return found;await delay(25);}throw Error('Missing protocol response');};
  const request=async(t,fields={})=>{const id=++rid;const meta=messages.findLast(m=>m._collect)?._collect;if(participant&&meta&&/^(g\.|b\.)/.test(t))ws.send(JSON.stringify({t:'collect.decision',rid:id,action:t,decision:{decisionId:'proof-'+id,decisionSeq:meta.seq,decisionEpoch:meta.epoch,matchId:meta.matchId,invokedAt:Date.now()}}));ws.send(JSON.stringify({t,...fields,rid:id}));return wait(m=>m.rid===id);};
  ws.send(JSON.stringify({t:'hello',name:'Proof',...(token?{token}:{})}));const welcome=await wait(m=>m.t==='welcome');
  return {ws,messages,request,wait,welcome};
}
test('public WS handles malformed JSON shapes, blocks unconsented start/modded bypass and retains partial games', {skip:!base,timeout:60000},async t=>{
  const collection=(await api('',{label:'Protocol and partial proof'})).value,id=collection.id;
  assert.equal((await api('/'+id+'/join',{consent:false})).status,400);
  const p=(await api('/'+id+'/join',{consent:true})).value;
  const a=await client(t,p),b=await client(t);
  for(const data of ['null','[]','{"t":"unknown"}','{broken']){a.ws.send(data);await delay(30);}
  assert.equal((await a.request('ping',{c:Date.now()})).t,'pong');
  assert.equal((await a.request('room.create',{mode:'coop',difficulty:'NORMAL'})).t,'ok');
  const room=(await a.wait(m=>m.t==='room.state')).code;
  assert.equal((await b.request('room.join',{code:room})).t,'ok');assert.equal((await b.request('room.ready',{ready:true})).t,'ok');
  const blocked=await a.request('room.start');assert.equal(blocked.t,'error');assert.match(blocked.detail,/Every human/);
  const mod=await a.request('room.setPlugins',{plugins:[{id:'proof-plugin',version:'1.0.0'}]});assert.equal(mod.t,'error');assert.match(mod.detail,/vanilla/);
  const script=await a.request('room.setMode',{modeId:'script_proof'});assert.equal(script.t,'error');
  // Explicit new consent followed by the original identity's reconnect; no Session semantics are replaced.
  const p2=(await api('/'+id+'/join',{consent:true})).value,token=b.welcome.token,playerId=b.welcome.playerId;b.ws.terminate();
  const resumed=await client(t,p2,token);assert.equal(resumed.welcome.playerId,playerId);
  await delay(100);assert.equal((await resumed.request('room.ready',{ready:true})).t,'ok');
  assert.equal((await a.request('room.start')).t,'ok');
  await a.wait(m=>m.t==='m.public');
  assert.equal((await a.request('g.infoReady',{setupRevision:999})).t,'error');
  // In server combat these reports are harmless/idempotently ignored; must never become human learning actions.
  a.ws.send(JSON.stringify({t:'b.progress',battleId:'expired',progress:{}}));
  await a.request('g.leave');await resumed.request('g.leave');await delay(150);
  const own=await api(`/${id}/timeline?match=${(await api('/'+id)).value.matches[0].id}&seat=0`,undefined,{organizer:false,participant:p});assert.equal(own.status,200);
  const otherSeat=await api(`/${id}/timeline?match=${(await api('/'+id)).value.matches[0].id}&seat=1`,undefined,{organizer:false,participant:p});assert.equal(otherSeat.status,403);
  assert.equal((await api('/'+id+'/export',{profile:'all-human'},{organizer:false,participant:p})).status,403);
  assert.deepEqual((await api('',undefined,{organizer:false})).value,[]);
  assert.deepEqual((await api('/'+id,undefined,{organizer:false})).value.matches,[]);
  const different=(await api('',{label:'Different organizer'})).value;
  assert.equal((await api(`/${id}/timeline?match=${(await api('/'+id)).value.matches[0].id}&seat=0`,undefined,{key:organizers.get(different.id)})).status,403);
  const done=(await api('/'+id)).value;assert.equal(done.matches.length,1);assert.equal(done.matches[0].complete,false);assert.equal(done.matches[0].victory,null);assert.equal(done.matches[0].status,'partial');
  const ref=(await api('/'+id+'/export',{profile:'all-human'})).value;assert.equal(ref.quality.samples,0);
  // A sealed collection remains exportable when its identity later returns to ordinary play.
  await client(t,null,a.welcome.token);await delay(80);
  assert.equal((await api('/'+id+'/export',{profile:'all-human'})).status,200);
  // A takeover DURING a recording is a genuine capture interruption.
  const active=(await api('',{label:'Active uninstrumented takeover'})).value,cap=(await api('/'+active.id+'/join',{consent:true})).value;
  const captured=await client(t,cap);await captured.request('room.create',{mode:'solo',difficulty:'ABYSS'});await captured.request('room.start');
  const bare=await client(t,null,captured.welcome.token);await bare.request('g.leave');await delay(100);
  const broken=(await api('/'+active.id)).value.matches[0];assert.equal(broken.status,'technical-failure');assert.equal(broken.complete,false);assert.equal(broken.victory,null);
  assert.equal((await api('/'+active.id+'/export',{profile:'all-human'})).status,400);
});
