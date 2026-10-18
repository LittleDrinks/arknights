import test from 'node:test';
import assert from 'node:assert/strict';
import { Net } from '../game/public/js/net.js';
import { installProbe } from '../arknights/collect/probe.mjs';
import { project, actionOf } from '../arknights/collect/projection.mjs';

test('real Net request freezes invocation watermark across queue, epoch change, and synchronous listeners', async t => {
  const sent=[], n=new Net({ timers:globalThis });
  n.status='reconnecting';n.name='Collector';n.ws={readyState:1,send:s=>{const m=JSON.parse(s);if(m.t!=='collect.seen')sent.push(m);}};
  const p=installProbe(n);
  n._onMessage(JSON.stringify({t:'m.public',phase:'PREP',_collect:{seq:10,epoch:'old',matchId:'m-game'}}));
  const queued=n.request('g.move',{uid:17,to:{area:'board',row:5,col:4},dir:'UP'});
  queued.catch(()=>{});
  assert.equal(sent.length,0);
  n._onMessage(JSON.stringify({t:'m.public',phase:'PREP',_collect:{seq:20,epoch:'new',matchId:'m-game'}}));
  n.status='online';n._flushQueue();
  assert.equal(sent[0].t,'collect.decision');assert.equal(sent[0].decision.decisionSeq,10);assert.equal(sent[0].decision.decisionEpoch,'old');
  const request=sent[1];assert.equal(request.t,'g.move');assert.equal(request.to.row,5);assert.equal(request.dir,'UP');
  n._onMessage(JSON.stringify({t:'ok',rid:request.rid,_collect:{seq:21,epoch:'new',matchId:'m-game'}}));await queued;
  let nested;
  n.on('m.private',()=>{nested=n.request('g.equip',{itemUid:9,targetUid:17,replaceUid:8});});
  n._onMessage(JSON.stringify({t:'m.private',playerId:'p_one',_collect:{seq:30,epoch:'new',matchId:'m-game'}}));
  assert.equal(sent[2].decision.decisionSeq,30,'listener sees the frame that triggered the decision');
  assert.deepEqual(sent[3],{t:'g.equip',rid:sent[3].rid,itemUid:9,targetUid:17,replaceUid:8});
  n._onMessage(JSON.stringify({t:'ok',rid:sent[3].rid}));await nested;
});

test('projection preserves dynamic draft/loadout/ops, piece lists and full manual parameters while excluding privileged sentinels',()=>{
  const observation=project({t:'m.public',phase:'BAND_DRAFT',hiddenBossId:'HIDDEN_SENTINEL',draft:{picks:{p_one:'band_a',p_two:'band_b'},skipsLeft:{p_one:2},turn:'p_one',turnSeconds:15}},
    {t:'m.private',playerId:'p_one',seed:'SEED_SENTINEL',spec:{power:'SPEC_SENTINEL'},otherPrivate:'OTHER_SENTINEL',loadout:{chess_1:{skill:2,module:'module_1'}},ops:{char_1:{potential:6,cultivate:3}},hand:[null,{uid:17,kind:'chess',id:'chess_1',items:[{uid:9,id:'item_1'}]}],board:[{uid:18,row:5,col:3,dir:'UP'}]},'p_one');
  assert.deepEqual(observation.public.draft.picks,{p_one:'band_a',p_two:'band_b'});
  assert.deepEqual(observation.own.loadout,{chess_1:{skill:2,module:'module_1'}});
  assert.deepEqual(observation.own.ops,{char_1:{potential:6,cultivate:3}});
  assert.deepEqual(observation.own.hand[1].items,[{uid:9,id:'item_1'}]);
  assert(!JSON.stringify(observation).includes('SENTINEL'));
  assert.deepEqual(actionOf({t:'g.move',uid:18,to:{area:'hand',idx:2},dir:'LEFT',rid:'a'}),{t:'g.move',uid:18,to:{area:'hand',idx:2},dir:'LEFT'});
  assert.deepEqual(actionOf({t:'g.equip',itemUid:9,targetUid:18,replaceUid:8}),{t:'g.equip',itemUid:9,targetUid:18,replaceUid:8});
});

test('human display names are absent from training views; game piece/operator names remain',()=>{
  const obs=project({t:'m.public',players:[{playerId:'p_one',name:'DISPLAY_NAME_PRIVATE',seat:0}]},
    {t:'m.private',playerId:'p_one',hand:[{uid:1,id:'chess_1',name:'Operator Name'}]},'p_one');
  assert.equal(obs.public.players[0].name,undefined);assert.equal(obs.own.hand[0].name,'Operator Name');
  assert(!JSON.stringify(obs).includes('DISPLAY_NAME_PRIVATE'));
});
