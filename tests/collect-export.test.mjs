import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { Collections, sha } from '../arknights/collect/store.mjs';
const root=path.resolve('results/tests');fs.mkdirSync(root,{recursive:true});
function fixture(t) {
  const dir=fs.mkdtempSync(path.join(root,'collect-export-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const store=new Collections(dir), c=store.create('privacy+watermark'), id=c.id;
  store.match(id,{id:'m-real',roomCode:'TEST',matchNo:1,seats:[{seat:0,playerId:'p_one',participantId:'p-seat'}]});
  const append=r=>store.append(id,'players',{matchId:'m-real',seat:0,playerId:'p_one',participantId:'p-seat',epoch:'e-1',...r});
  append({kind:'frame',clientSeq:1,message:{t:'m.public',phase:'PREP',round:1,hiddenBossId:'SECRET_HIDDEN',seed:'SECRET_SEED',spec:{raw:'SECRET_SPEC'}}});
  append({kind:'frame',clientSeq:2,message:{t:'m.private',playerId:'p_one',funds:7,hand:[{uid:5,id:'chess_1'}],otherPrivate:'OTHER_SEAT_PRIVATE',token:'TOKEN_SENTINEL',ip:'IP_SENTINEL'}});
  append({kind:'frame',clientSeq:3,message:{t:'m.private',playerId:'p_one',funds:999}});
  append({kind:'request',requestId:'req-1',source:'human',message:{t:'g.move',uid:5,to:{area:'board',row:4,col:3},dir:'UP'},decision:{decisionSeq:2,decisionEpoch:'e-1',matchId:'m-real'},flushSeq:3});
  append({kind:'ack',requestId:'req-1',status:'accepted'});
  store.append(id,'verification',{kind:'handled',matchId:'m-real',requestId:'req-1',playerId:'p_one',epoch:'e-1',action:{t:'g.move',uid:5,to:{area:'board',row:4,col:3},dir:'UP'},status:'accepted',seed:'SECRET_SEED'});
  store.finish(id,'m-real',{victory:false,reason:'eliminated',errors:0,players:[{playerId:'p_one',alive:false}]});
  return {store,id,append};
}
test('public export uses invocation watermark, revisable interval references, immutable dataset hashes and privacy boundary',t=>{
  const {store,id}=fixture(t), rows=store.rows(id,'players'), original=fs.readFileSync(store.file(id,'players.jsonl'));
  const a=store.annotate(id,{matchId:'m-real',seat:0,start:rows[0].eventId,end:rows.at(-1).eventId,goalCore:'炎',stage:'prep',quality:'uncertain'});
  assert.equal(store.export(id).quality.samples,0);
  store.annotate(id,{...a,quality:'great',note:'actual manual move'});
  const ref=store.export(id), bytes=fs.readFileSync(path.join(ref.path,'samples.jsonl')), s=JSON.parse(bytes.toString());
  assert.equal(s.observation.own.funds,7,'post-decision funds=999 must not leak from flush watermark');
  assert.equal(s.annotations[0].revision,2);assert.equal(s.action.to.col,3);assert.equal(s.action.dir,'UP');assert.equal(s.outcome.teamVictory,false);
  assert.equal(ref.sha256,sha(bytes));assert.equal(store.export(id).version,ref.version);
  assert.deepEqual(fs.readFileSync(store.file(id,'players.jsonl')),original);
  assert(!JSON.stringify(ref).includes('SECRET_'));assert(!bytes.toString().includes('SENTINEL'));assert(!bytes.toString().includes('OTHER_SEAT'));
  assert(!fs.readFileSync(store.file(id,'players.jsonl'),'utf8').includes('TOKEN_SENTINEL'));
  assert.equal(ref.quality.actionCoverage['g.move'].parameterized,1);
  assert.equal(ref.quality.actionCoverage['g.move'].shortlisted,null);
});
test('unknown ack, partial captures and technical failures never become certified complete wins',t=>{
  const {store,id,append}=fixture(t);
  append({kind:'request',requestId:'req-lost',source:'human',message:{t:'g.ready',ready:true},decision:{decisionSeq:2},flushSeq:3});
  store.append(id,'verification',{kind:'handled',matchId:'m-real',requestId:'req-lost',playerId:'p_one',epoch:'e-1',action:{t:'g.ready',ready:true},status:'accepted'});
  assert.equal(store.export(id,'all-human').quality.samples,1);
  store.match(id,{id:'m-partial',roomCode:'TEST',seats:[{seat:0,playerId:'p_one'}]});
  store.finish(id,'m-partial',null);
  store.match(id,{id:'m-broken',roomCode:'TEST',seats:[{seat:0,playerId:'p_one'}]});
  store.finish(id,'m-broken',{victory:true,reason:'victory',errors:1});
  const matches=store.get(id).matches;
  assert.equal(matches[1].status,'partial');assert.equal(matches[1].victory,null);
  assert.equal(matches[2].status,'technical-failure');assert.equal(matches[2].complete,false);assert.equal(matches[2].victory,null);
});
test('restart marks interrupted matches partial; export processes do not interrupt live capture',t=>{
  const {store,id}=fixture(t);store.match(id,{id:'m-live',roomCode:'TEST',seats:[]});
  new Collections(path.resolve(store.root,'../..'));assert.equal(store.get(id).matches.at(-1).status,'recording');
  new Collections(path.resolve(store.root,'../..'),{recover:true});assert.equal(store.get(id).matches.at(-1).status,'partial');
});

test('a dead human carried by a victorious team retains personal loss, elimination round and contribution',t=>{
  const {store,id}=fixture(t), manifest=store.get(id);
  // A new real outcome is represented by a separate match, never by rewriting the first game's raw events.
  store.match(id,{id:'m-carried',roomCode:'TEAM',matchNo:1,seats:[{seat:0,playerId:'p_one',participantId:'p-seat'}]});
  for(const row of store.rows(id,'players')) {const {eventId,chain,...data}=row;store.append(id,'players',{...data,matchId:'m-carried',requestId:data.requestId==='req-1'?'req-carried':data.requestId,decision:data.decision?{...data.decision,matchId:'m-carried'}:undefined});}
  store.append(id,'verification',{kind:'handled',matchId:'m-carried',requestId:'req-carried',playerId:'p_one',epoch:'e-1',action:{t:'g.move',uid:5,to:{area:'board',row:4,col:3},dir:'UP'},status:'accepted'});
  store.finish(id,'m-carried',{victory:true,reason:'victory',errors:0,players:[{seat:0,playerId:'p_one',isBot:false,victory:false,alive:false,eliminatedRound:8,roundsPassed:7,stats:{bossDamage:123}}]});
  const ref=store.export(id,'all-human'), rows=fs.readFileSync(path.join(ref.path,'samples.jsonl'),'utf8').trim().split('\n').map(JSON.parse), sample=rows.find(r=>r.matchId==='m-carried');
  assert.equal(sample.outcome.teamVictory,true);assert.equal(sample.outcome.playerVictory,false);assert.equal(sample.outcome.alive,false);
  assert.equal(sample.outcome.eliminatedRound,8);assert.equal(sample.outcome.bossDamage,123);
  assert.equal(ref.quality.outcomeCounts.teamVictories,1);assert.equal(ref.quality.outcomeCounts.playerVictories,0);assert.equal(ref.quality.outcomeCounts.carriedEliminated,1);
});
test('journal truncation/tampering and changed immutable datasets cannot be silently exported',t=>{
  const {store,id}=fixture(t),ref=store.export(id,'all-human');
  fs.writeFileSync(path.join(ref.path,'samples.jsonl'),'changed');assert.throws(()=>store.export(id,'all-human'),/integrity/);
  const file=store.file(id,'players.jsonl');fs.writeFileSync(file,fs.readFileSync(file,'utf8').replace('"funds":7','"funds":777'));
  assert.throws(()=>store.export(id,'all-human'),/integrity/);
});

test('a restart preserves interrupted battle bytes and recovers every complete journal frame as partial', t => {
  const {store,id}=fixture(t),dir=path.resolve(store.root,'../..');
  const rec={name:'m-interrupted-r1-0',round:1,kind:'normal',field:{fieldId:'n:p_one'},participants:['p_one'],frames:[]};
  store.battleFrame(id,rec,{snap:{t:0,units:[]},ev:[['start']]});
  store.battleFrame(id,rec,{snap:{t:.1,units:[]},ev:[]});
  const journal=store.file(id,'battles/'+rec.name+'.frames.jsonl');fs.appendFileSync(journal,'{"partial":');
  const original=fs.readFileSync(journal);new Collections(dir,{recover:true});
  const recovered=JSON.parse(fs.readFileSync(store.file(id,'battles/'+rec.name+'.json')));
  assert.equal(recovered.complete,false);assert.equal(recovered.result,null);assert.deepEqual(recovered.frames.map(f=>f.snap.t),[0,.1]);
  assert.deepEqual(fs.readFileSync(journal),original);
  new Collections(dir,{recover:true});assert.deepEqual(fs.readFileSync(journal),original);
});
