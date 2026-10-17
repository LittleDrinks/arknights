// Synthetic contracts only: no Match, Battle, fixture grants, or gameplay runs.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { transform, load } from './loader.mjs';
import * as p from './policy.mjs';
const data = JSON.parse(fs.readFileSync(new URL('../../Stronghold-Protocol/data/chess.json', import.meta.url)));
const cfg = build => p.options({ARK_BUILD_TEACHER:build});
const chess = n => Object.entries(data).find(([,c]) => c.name === n && c.visible && !c.isDiy && !c.isGolden)?.[0];
function state(names, { deployed = true } = {}) {
  const pieces = names.map((n,i) => ({uid:i+1,id:chess(n),kind:'chess',items:[]}));
  assert(pieces.every(x => x.id));
  const ps = {gd:{chess:id=>data[id], baseIdOf:id=>data[id]?.baseId ?? id.replace(/_b$/,'_a'),
    isGolden:id=>!!data[id]?.isGolden, modeInactiveBonds:new Set(), equipPerChess:2},
    board:new Map(deployed ? pieces.map((x,i)=>[`10,${i+2}`,x]) : []),
    hand:deployed ? [null,null] : pieces, temp:[], bonds:{}, layers:{}, funds:0,
    allChess(){return [...this.board.values(),...this.hand,...this.temp].filter(x=>x?.kind==='chess');},
    find(uid){
      for(const [key,piece] of this.board) if(piece.uid===uid)return {area:'board',key,piece};
      for(const area of ['hand','temp']){const idx=this[area].findIndex(x=>x?.uid===uid);if(idx>=0)return {area,idx,piece:this[area][idx]};}
      return null;
    }};
  Object.defineProperty(ps,'tempEmpty',{get(){return this.temp.every(x=>!x);}});
  return ps;
}
test('guard rejects drift/double transform; native imports retain valid syntax; off loader is identity', async () => {
  const source = fs.readFileSync(new URL('../../Stronghold-Protocol/server/match/bot.js',import.meta.url),'utf8');
  const transformed = transform(source);
  assert.throws(()=>transform(source+'\n'),/SHA256/);
  assert.throws(()=>transform(transformed),/SHA256/);
  assert.equal((transformed.match(/__build.safeSell\(/g)||[]).length,6);
  const check=spawnSync(process.execPath,['--input-type=module','--check'],{input:transformed,encoding:'utf8'});
  assert.equal(check.status,0,check.stderr);
  const original={format:'module',source};
  assert.strictEqual(await load(new URL('../../Stronghold-Protocol/server/match/bot.js',import.meta.url).href,{},async()=>original),original);
});
test('configuration fails closed, all toggles can independently be disabled, banned faction falls back', () => {
  assert.equal(p.options({}).on,false);
  assert.throws(()=>p.options({ARK_BUILD_TEACHER:'sargon',ARK_BUILD_ROSTER:'true'}));
  const ps=state(['至简']);
  const off=p.options({ARK_BUILD_TEACHER:'sargon',ARK_BUILD_ROSTER:'0',ARK_BUILD_EQUIPMENT:'0',ARK_BUILD_ADJACENCY:'0'});
  assert.equal(p.buyBonus(ps,chess('缇缇'),off),0);
  assert.equal(p.protection(ps,ps.allChess()[0],off),0);
  assert.equal(p.itemBonus(ps,p.ITEMS.tea+'_a',off),0);
  ps.m={disabledBonds:['sargonShip']};
  assert.equal(p.strategy(ps,cfg('sargon')),null);
  assert.equal(p.buyBonus(ps,chess('缇缇'),cfg('sargon')),0);
});
test('current cheap faction fillers get bias, elite ownership does not invite duplicate cycles; healer matters', () => {
  const ps=state(['至简']);
  const filler=Object.values(data).find(c=>c.visible&&!c.isGolden&&!c.isDiy&&c.tier<=2&&c.bonds?.includes('sargonShip'));
  assert(filler);
  assert(p.buyBonus(ps,filler.chessId,cfg('sargon'))>=20);
  ps.allChess()[0].id=data[chess('至简')].goldenId;
  assert(p.buyBonus(ps,chess('至简'),cfg('sargon'))<0);
  const healer=Object.values(data).find(c=>c.visible&&!c.isGolden&&!c.isDiy&&(c.dmgType==='heal'||c.attackKind==='heal'));
  const heal={uid:99,id:healer.chessId,kind:'chess',items:[]}; ps.hand[0]=heal;
  const b={sargonShip:{count:3,active:true}};
  assert(p.lineupBonus(ps,[...ps.board.values(),heal],b,cfg('sargon'))>p.lineupBonus(ps,[...ps.board.values()],b,cfg('sargon'))+20);
});
test('compass and tea co-locate; wrong/duplicate equipment cannot consume the reserved slot', () => {
  const ps=state(['至简','异客']), c=cfg('sargon');
  const zh=ps.allChess()[0];
  assert.strictEqual(p.equipmentTarget(ps,{id:p.ITEMS.tea+'_a'},c),zh);
  zh.items.push({id:p.ITEMS.tea+'_a'});
  assert.strictEqual(p.equipmentTarget(ps,{id:p.ITEMS.compass+'_b'},c),zh);
  assert(p.reserved(ps,zh,{id:p.ITEMS.arm+'_a'},c));
  assert(p.reserved(ps,zh,{id:p.ITEMS.tea+'_b'},c));
  const k=state(['圣聆初雪','锏']);
  assert.equal(p.equipmentTarget(k,{id:p.ITEMS.tears+'_a'},cfg('kjerag')).id,chess('圣聆初雪'));
  const onlyItems=p.options({ARK_BUILD_TEACHER:'sargon',ARK_BUILD_ROSTER:'0'});
  assert.strictEqual(p.equipmentTarget(ps,{id:p.ITEMS.compass+'_a'},onlyItems),zh);
});
test('adjacency respects active skillful and the 40-layer four/eight-neighbor boundary', () => {
  const ps=state(['圣聆初雪','灵知']), [holy,gnosis]=ps.allChess(), plan=new Map([[gnosis.uid,'10,3']]);
  const bonus=key=>p.placementBonus(ps,holy,key,plan,[holy,gnosis],100,new Set(['10,3']),cfg('kjerag'));
  assert.equal(bonus('10,2'),0);
  ps.bonds.skillfulShip={active:true};
  assert.equal(bonus('10,2'),4);
  assert.equal(bonus('11,2'),0);
  ps.layers.skillfulShip=40;
  assert.equal(bonus('11,2'),4);
});
test('overflow preservation uses actual sale API/refund; when all protected it explicitly downgrades, never discards chess', () => {
  const ps=state(['至简','缇缇'],{deployed:false}), c=cfg('sargon');
  ps.temp=[ps.hand.pop()];
  ps.move=()=>({ok:false});
  const sales=[];
  ps.sell=uid=>{const loc=ps.find(uid); sales.push(uid);ps[loc.area][loc.idx]=null;ps.funds+=1;return {ok:true};};
  ps.resolveTemp=()=>assert(!ps.temp.some(x=>x?.kind==='chess'),'must not discard chess');
  assert.equal(p.safeSell(ps,ps.hand[0].uid,c).ok,false);
  assert.equal(sales.length,0);
  p.finishTemp(ps,c);
  assert(sales.length>0);
  assert.equal(ps.funds,sales.length);
  assert(p.reports(ps).events.some(e=>e.kind==='overflow-sale'&&e.priority>0&&e.refund===1));
});
