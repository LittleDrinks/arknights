import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { Match } from '../game/server/match/Match.js';
import { Battle } from '../game/server/sim/Battle.js';
import { getData } from '../game/server/data.js';
import { Collector } from '../arknights/collect/server.mjs';
import { validateRecordings } from '../arknights/replay/export.mjs';
const quiet={info(){},warn(){},debug(){},error(){}};
const scrub=value=>JSON.parse(JSON.stringify(value,(k,v)=>['serverNow','deadline','turnDeadline','durationMs'].includes(k)?undefined:v));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const data=getData({log:quiet});
function fixture(t){const base=path.resolve('results/tests');fs.mkdirSync(base,{recursive:true});const dir=fs.mkdtempSync(path.join(base,'collect-match-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return new Collector(dir);}
async function run(t, collector, record, fault=false) {
  const c=collector.store.create('RealScheduler parity'), p=collector.join(c.id,true);
  collector.players.set('p_test',collector.participants.get(p.key));
  const transitions=[],latencies=[];let terminal;
  const C=record?collector.matchClass(Match,Battle):Match;
  const m=new C({roomCode:'PAIR',matchNo:1,seed:106,mode:'solo',difficulty:'ABYSS',data,
    // Production scheduler, identical shortened engine test pacing in both arms (not the browser acceptance pacing).
    timerScale:.01,combatSpeed:200,botRehearsal:0,clientCombat:false,
    seats:[{seat:0,playerId:'p_test',name:'Test',isBot:false,connected:true}],log:quiet,
    send(){return true;},broadcast(){},onEnd:s=>{terminal=s;}});
  t.after(()=>{if(!m.disposed)m.dispose();});assert.equal(m.sched.virtual,false);
  const action=msg=>{const start=performance.now(),res=m.handle('p_test',msg);latencies.push(performance.now()-start);transitions.push({msg,res,public:scrub(m.publicView()),own:scrub(m.players.get('p_test').privateView())});return res;};
  m.start();action({t:'g.infoReady',setupRevision:m.setupRevision+1});action({t:'g.infoReady',setupRevision:m.setupRevision});

  let last=-1, injected=false;const started=Date.now();
  while(!terminal) {
    assert(Date.now()-started<60000,'short-pacing test stalled');
    if(m.phase==='BAND_DRAFT'&&!m.players.get('p_test').bandId)action({t:'g.band',bandId:'band_bldsk'});
    if(m.phase==='PREP'&&last!==m.round) {
      last=m.round;action({t:'g.buy',slot:99});
      if(fault&&record&&!injected) {
        injected=true;
        const file=collector.store.file(c.id,'players.jsonl');fs.renameSync(file,file+'.retained');fs.mkdirSync(file);
        assert.equal(collector.store.append(c.id,'players',{kind:'fault-probe'}),false);
      }
      action({t:'g.ready',ready:true});
    }
    if(m.phase==='SP_DRAFT'&&m.spTurn()==='p_test')action({t:'g.choice',idx:0});
    await delay(2);
  }
  m.dispose();
  latencies.sort((a,b)=>a-b);
  return {transitions,terminal:scrub(terminal),collection:c.id,latency:{p50:latencies[Math.floor(latencies.length*.5)],p95:latencies[Math.floor(latencies.length*.95)]}};
}
test('recording off/on leaves seeded public actions, errors and actual natural outcome equivalent using RealScheduler', {timeout:90000},async t=>{
  const collector=fixture(t),off=await run(t,collector,false),on=await run(t,collector,true);
  assert.deepEqual(on.transitions,off.transitions);assert.deepEqual(on.terminal,off.terminal);
  const meta=collector.store.get(on.collection).matches[0];assert.equal(meta.complete,true);assert.equal(meta.victory,false);
  const recordings=collector.battles(on.collection,meta.id);assert(recordings.length>0);validateRecordings(recordings);
  assert(collector.store.rows(on.collection,'verification').some(r=>r.kind==='operation'&&r.source==='human'));
  const result=path.resolve('results/tests/collect-equivalence.json');fs.writeFileSync(result,JSON.stringify({pass:true,pacing:'RealScheduler, equal engine test pacing timerScale=.01/combatSpeed=200; browser acceptance uses default pacing',off:off.latency,on:on.latency,battles:recordings.length,terminal:on.terminal},null,2));
});
test('capture write failure lets the real game settle but latches technical-failure and forbids a certified dataset', {timeout:90000},async t=>{
  const collector=fixture(t),on=await run(t,collector,true,true),meta=collector.store.get(on.collection).matches[0];
  assert.equal(meta.status,'technical-failure');assert.equal(meta.complete,false);assert.equal(meta.victory,null);
  assert.throws(()=>collector.store.export(on.collection),/storage failed/);
});

test('native bot and autoplay actions retain their actual source even when the only human occupies seat 2', {timeout:30000},async t=>{
  const collector=fixture(t), c=collector.store.create('source attribution'),p=collector.join(c.id,true);
  collector.players.set('p_seat2',collector.participants.get(p.key));
  const C=collector.matchClass(Match,Battle),m=new C({roomCode:'AUTO',matchNo:1,seed:106,mode:'coop',difficulty:'ABYSS',data,
    timerScale:.02,combatSpeed:200,botRehearsal:0,clientCombat:false,
    seats:Array.from({length:4},(_,seat)=>({seat,playerId:seat===2?'p_seat2':'ai_'+seat,name:'Seat'+seat,isBot:seat!==2,connected:true})),
    log:quiet,send(){return true;},broadcast(){},onEnd(){}});
  try {
    m.start();assert.equal(m.handle('p_seat2',{t:'g.autoplay',on:true}).ok,true);
    const started=Date.now();let operations;
    for(;;) {
      operations=collector.store.rows(c.id,'verification').filter(r=>r.kind==='operation');
      if(operations.some(r=>r.seat===2&&r.source==='autoplay'&&r.action.t==='g.buy')&&operations.some(r=>r.seat===0&&r.source==='bot'))break;
      assert(Date.now()-started<15000,'native auto prep did not execute');await delay(10);
    }
    assert(operations.filter(r=>r.seat===2).every(r=>r.source!=='human'));
    assert(operations.filter(r=>r.seat===0).every(r=>r.source!=='human'));
    m.onDisconnect('p_seat2');m.onReconnect('p_seat2');
    const lifecycle=collector.store.rows(c.id,'verification');assert(lifecycle.some(r=>r.kind==='onDisconnect'));assert(lifecycle.some(r=>r.kind==='onReconnect'));
  }finally{m.dispose();}
  const meta=collector.store.get(c.id).matches[0];assert.equal(meta.complete,false);assert.equal(meta.victory,null);
});
