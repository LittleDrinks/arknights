// Small, explicitly constructed mechanism fixtures, not naturally obtained full-game demonstrations.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.argv[2]);
const out = path.resolve(process.argv[3]);
const { makeMatch, give } = await import(pathToFileURL(path.join(root, 'test/match/harness.js')));
const chess = JSON.parse(fs.readFileSync(path.join(root,'data/chess.json'),'utf8'));
const examples = [];
function fixture(names) {
  const h = makeMatch({ mode: 'solo', difficulty: 'ABYSS', seed: 1 });
  h.start(); h.toPrep(1, { band: 'band_sarkazb' });
  const p = h.ps('p_0'), m = h.m;
  const tiles = [[10,4], [11,4], [12,4], [10,5]];
  names.forEach((name, i) => {
    const id = Object.keys(chess).find(id => { const c = chess[id]; return c.name === name && c.visible && !c.isGolden; });
    assert.ok(id, name); give(m, p, id, 'board', tiles[i]);
  });
  p.funds = 20;
  return { h, m, p };
}
for (const count of [1, 3, 6]) {
  const {h,m,p} = fixture(['拉普兰德','阿罗玛','安洁莉娜','莫斯提马']);
  assert.ok(p.bonds.siracusaShip.active && p.bonds.arcaneShip.active);
  p.shop.freeRefreshes = count;
  const actions = [];
  for (let i=0; i<count; i++) {
    const result = m.handle(p.playerId, {t:'g.refresh'}); assert.equal(result.ok,true);
    actions.push({action:{t:'g.refresh'}, layers:{...p.layers}});
  }
  m.dispatch(p,'onPrepEnd',{});
  const expected = count === 1 ? {siracusaShip:10,arcaneShip:2} : {siracusaShip:22,arcaneShip:6};
  assert.equal(p.layers.siracusaShip,expected.siracusaShip);
  assert.equal(p.layers.arcaneShip,expected.arcaneShip);
  assert.equal(h.logs.error.length,0);
  examples.push({name:`mixed-refresh-${count}`,fixture:true,policy_obtained:false,names:['拉普兰德','阿罗玛','安洁莉娜','莫斯提马'],freeRefreshes:count,actions,afterPrepEnd:{...p.layers},expected});
  m.dispose();
}
{
  const {h,m,p} = fixture(['夕','小满','诗怀雅','阿罗玛']);
  assert.ok(p.bonds.yanShip.active && p.bonds.arcaneShip.active);
  const actions=[];
  for(let i=0;i<2;i++) {
    p.shop.slots[0]={kind:'chess',id:'chess_char_1_07_a',basePrice:2,sold:false,frozen:false};
    const before = new Set(p.allChess().map(p=>p.uid));
    let result=m.handle(p.playerId,{t:'g.buy',slot:0});assert.equal(result.ok,true);
    actions.push({action:{t:'g.buy',slot:0},funds:p.funds});
    const bought=p.allChess().find(p=>!before.has(p.uid));assert.ok(bought);
    result=m.handle(p.playerId,{t:'g.sell',uid:bought.uid});assert.equal(result.ok,true);
    actions.push({action:{t:'g.sell',uid:bought.uid},funds:p.funds});
  }
  m.dispatch(p,'onPrepEnd',{});
  assert.equal(p.layers.yanShip,6);assert.equal(p.layers.arcaneShip,4);assert.equal(p.funds,18);assert.equal(h.logs.error.length,0);
  examples.push({name:'mixed-acquire-yan-arcane',fixture:true,policy_obtained:false,names:['夕','小满','诗怀雅','阿罗玛'],actions,afterPrepEnd:{...p.layers},fundsBefore:20,fundsAfter:p.funds,netFunds:-2,warning:'This is a paid acquisition engine, not an infinite-profit loop. Initial board and offers were constructed fixtures.'});
  m.dispose();
}
{
  const {h,m,p} = fixture(['拉普兰德','阿罗玛','安洁莉娜','莫斯提马']);
  p.shop.freeRefreshes=2;
  assert.equal(m.handle(p.playerId,{t:'g.refresh'}).ok,true);
  assert.equal(p.layers.siracusaShip,4);
  p.shop.slots[0]={kind:'chess',id:'chess_char_2_16_a',basePrice:2,sold:false,frozen:false};
  assert.equal(m.handle(p.playerId,{t:'g.buy',slot:0}).ok,true);
  assert.equal(m.handle(p.playerId,{t:'g.refresh'}).ok,true);
  assert.equal(p.layers.siracusaShip,8);
  assert.equal(h.logs.error.length,0);
  examples.push({name:'lapland-per-copy-first-refresh',fixture:true,policy_obtained:false,
    actions:[{t:'g.refresh'},{t:'g.buy',slot:0},{t:'g.refresh'}],
    afterActions:{...p.layers},expected:{siracusaShip:8},
    explanation:'Existing Lappland fires once; a newly acquired normal copy in reserve fires on its own first witnessed refresh. This is not a global once-per-player-round limit.'});
  m.dispose();
}
const result={commit:'6ab2468279e926d107788f63d7fe219b85f67b4a',kind:'constructed mechanism checks; not full-game training success',examples};
fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,JSON.stringify(result,null,2));
console.log(JSON.stringify(result,null,2));
