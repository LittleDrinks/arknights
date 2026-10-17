// CONSTRUCTED R14 only. No shop trajectory, no natural-run victory claim.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
const ROOT = path.resolve(import.meta.dirname, '../../Stronghold-Protocol');
const PIN = '6ab2468279e926d107788f63d7fe219b85f67b4a';
assert.equal(execFileSync('git', ['-C', ROOT, 'rev-parse', 'HEAD'], {encoding:'utf8'}).trim(), PIN);
assert.equal(execFileSync('git', ['-C', ROOT, 'status', '--porcelain', '--untracked-files=all', '--', 'server', 'shared', 'data', 'test/match/harness.js'], {encoding:'utf8'}).trim(), '');
const imp = f => import(pathToFileURL(path.join(ROOT, f)));
const {makeMatch, give, giveItem, DATA} = await imp('test/match/harness.js');
const {arrange} = await imp('server/match/bot.js');
const {checkLoadout} = await imp('shared/protocol.js');
const E = {arm:'chess_item_5_03_e_b', tears:'chess_item_6_06_e_b', ice:'chess_item_5_02_e_b',
  compass:'chess_item_6_07_e_b', tea:'chess_item_2_04_e_b', morph:'chess_item_6_09_e_b', knife:'chess_item_3_04_e_b'};
// Explicit guide-derived adaptations to the pinned preset roster; no DIY/invented operators.
const builds = {
  kjerag: {core:'kjeragShip', guides:['BV1xVdBBWEuu','BV1X3XUB9EQb'],
    names:['角峰','初雪','灵知','凛御银灰','圣聆初雪','锏','空弦','夕'],
    layers:['kjeragShip','skillfulShip','arcaneShip','swiftShip'],
    equip:{'圣聆初雪':['arm','tears'],'空弦':['ice','tea'],'锏':['arm','tea']}},
  kazimierz: {core:'kazimierzShip', guides:['BV1g9AGz1EbT','BV1bAd4BhEJa'],
    names:['瑕光','砾','远牙','玛恩纳','耀骑士临光','锏','伊内丝','莱恩哈特'],
    layers:['kazimierzShip','preciShip','swiftShip','raidShip'],
    skills:{'瑕光':1,'耀骑士临光':1,'玛恩纳':2},
    equip:{'玛恩纳':['tea','arm'],'锏':['tea','arm'],'莱恩哈特':['tea','arm']}},
  sargon: {core:'sargonShip', guides:['BV16YSUBZETc'],
    names:['至简','缇缇','异客','佩佩','百炼嘉维尔','卡涅利安','莱恩哈特','空弦'],
    layers:['sargonShip','preciShip','swiftShip'],
    equip:{'至简':['compass','tea'],'异客':['arm','tea'],'佩佩':['arm','tea']}},
  yan: {core:'yanShip', guides:['BV1kgwjzqExY'],
    names:['小满','录武官','夕','烛煌','余','仇白','缪尔赛思','空弦'],
    layers:['yanShip','skillfulShip'],
    equip:{'缪尔赛思':['morph','knife'],'空弦':['morph','knife'],'烛煌':['arm','tea'],'仇白':['arm','tea']}}
};
const [buildName, layerText, variant='bot'] = process.argv.slice(2);
const cfg = builds[buildName], layer = Number(layerText);
assert(cfg && [200,500,999].includes(layer));
assert(['bot','adjacent','no-items','core-only','no-compass','attribution','attribution-full'].includes(variant));
const chess = name => {
  const choices = Object.values(DATA.chess).filter(c=>c.name===name && c.visible && !c.isGolden && !c.isDiy);
  assert.equal(choices.length, 1, `unique visible preset for ${name}`);
  return choices[0];
};
const records = cfg.names.map(chess);
const requestedLoadout = Object.fromEntries(Object.entries(cfg.skills||{}).map(([n,skill])=>[chess(n).chessId,{skill}]));
const lo = checkLoadout(requestedLoadout, id=>DATA.chess[id]);
assert.equal(lo.ok, true);
let h;
// Select a legal ban/stock seed BEFORE any battle; never disable bans or alter the pool.
for (let seed=1; seed<=1000; seed++) {
  const trial = makeMatch({mode:'solo',difficulty:'ABYSS',seed,captureFrames:false,
    seats:[{seat:0,playerId:'p_0',name:'CONSTRUCTED',isBot:false,connected:true,loadout:lo.loadout}]});
  if (cfg.layers.every(b=>!trial.m.disabledBonds.includes(b)) && records.every(c=>trial.m.pool.left(c.chessId)>=trial.m.gd.goldenCopies)) {h=trial;break;}
  trial.m.dispose(); trial.sched.dispose();
}
assert(h, 'no compatible seed found');
h.start(); h.toPrep(1);
const m=h.m, ps=h.ps('p_0');
// Cleanup uses the legal APIs; funds/items/chess/levels/layers below are fixture state, not earned play.
for (const p of [...ps.allChess()]) assert.equal(ps.sell(p.uid).ok,true);
for (const p of [...ps.hand,...ps.temp].filter(p=>p?.kind==='item')) assert.equal(ps.destroy(p.uid).ok,true);
m.round=14; ps.shop.level=6; m.bossId='boss_2';
assert(m.gd.mode.rounds['14'].bossTemplates[m.bossId]);
m._planBossWaves(); ps.invalidateDeployMap(); ps.recompute();
const pieces={};
for (const c of records) {
  assert(ps.hand.includes(null),'hand slot available');
  const p=give(m,ps,c.goldenId); assert.equal(p.poolCopies,m.gd.goldenCopies);
  pieces[c.name]=p;
}
arrange(m,ps);
assert.equal(ps.deployCount,8,'all eight prescribed operators must deploy');
if (variant!=='no-items') for (const [name,items] of Object.entries(cfg.equip)) for (const key of items) {
  if (variant==='no-compass' && key==='compass') continue;
  const p=giveItem(m,ps,E[key],'temp'); assert.equal(ps.equip(p.uid,pieces[name].uid).ok,true);
}
ps.layers=Object.fromEntries((variant==='core-only'?[cfg.core]:cfg.layers).map(id=>[id,layer]));
ps.recompute();
// Same original arranger, now aware of the final equipment/bonds/layers, no rehearsal battles.
arrange(m,ps);
if (variant==='adjacent') {
  assert.equal(buildName,'kjerag');
  const loc=ps.find(pieces['灵知'].uid), [r,c]=loc.key.split(',').map(Number);
  let moved=false;
  for (const [dr,dc] of [[0,-1],[0,1],[-1,0],[1,0],[-1,-1],[-1,1],[1,-1],[1,1]]) {
    if (ps.move(pieces['圣聆初雪'].uid,{area:'board',row:r+dr,col:c+dc},'RIGHT').ok) {moved=true;break;}
  }
  assert(moved,'legal adjacency move'); ps.recompute();
}
assert.equal(ps.deployCount,8);
assert(ps.bonds[cfg.core].active && ps.bonds[cfg.core].count >= (buildName==='yan'?9:6));
for (const id of cfg.layers) assert(ps.bonds[id].active, `required side bond inactive: ${id}`);
h.invariants();
const input=ps.battleInput();
const setup={kind:'CONSTRUCTED_R14_NOT_NATURAL',pin:PIN,build:buildName,layer,variant,guides:cfg.guides,seed:m.seed,
  bossId:m.bossId,stageId:m.stageId,bans:m.disabledBonds,bandId:ps.bandId,lpAtStart:ps.lp,deployCap:ps.deployCap,
  equipmentSlots:m.gd.equipPerChess,roster:input.units.map(u=>({...u,name:DATA.chess[u.chessId]?.name})),
  bonds:input.bonds,loadout:ps.loadout,playerEffects:input.playerEffects,waves:m.bossWaves};
fs.writeFileSync(path.join(import.meta.dirname,'results',`${buildName}-${layer}-${variant}.input.json`),JSON.stringify(setup,null,2)+'\n');
// Enter the original final-assault path: real Battle, original waves, damage/HP and Match LP/overtime handling.
// Stop at the R14 field end; do not advance to hidden core or publish a full-match victory.
m.hiddenLayerSum=ps.activatedLayers();
m.startFinalAssault(false);
assert.equal(m.bossPool.maxHp,3000000);
const b=m.fields[0].battle;
assert.equal(b.constructor.name,'Battle');
const metrics={hitCapCount:0,maxCancelledHit:0,skillStarts:{},leaks:[],poolTimeline:[],maxStats:{}};
if(variant.startsWith('attribution')) {
  metrics.unownedPoolDamage=0;
  const damage=m.bossPool.damage.bind(m.bossPool);
  m.bossPool.damage=(pid,amount)=>{const dealt=damage(pid,amount);if(pid==null) metrics.unownedPoolDamage+=dealt;return dealt;};
  metrics.uncreditedBossHits=[];
  b.on('damaged',c=>{
    if(c.target?.bossPool && !(c.credit?.side==='ally' && c.credit.ownerId!=null) && metrics.uncreditedBossHits.length<80)
      metrics.uncreditedBossHits.push({time:b.time,amount:c.amount,source:c.source?{id:c.source.defId,side:c.source.side,owner:c.source.ownerId}:null,
        credit:c.credit?{id:c.credit.defId,side:c.credit.side,owner:c.credit.ownerId}:null,dmg:c.dmg});
  },{owner:'audit-attribution',priority:-9999});
}
const fx=b.fx.bind(b);
b.fx=(kind,payload)=>{if(kind==='hitCap'){metrics.hitCapCount++;metrics.maxCancelledHit=Math.max(metrics.maxCancelledHit,payload.n);}return fx(kind,payload);};
b.on('skillStart',({unit})=>{if(unit.ownerId==='p_0') metrics.skillStarts[unit.defId]=(metrics.skillStarts[unit.defId]||0)+1;},{owner:'audit-observer',priority:-9999});
b.on('enemyLeak',({enemy})=>metrics.leaks.push({time:b.time,id:enemy.defId,lpr:enemy.lpr}),{owner:'audit-observer',priority:-9999});
let nextSample=0;
const tick=m.runner.onTick;
m.runner.onTick=runner=>{
  tick(runner);
  if(b.time>=nextSample || b.finished){
    metrics.poolTimeline.push({time:b.time,hp:m.bossPool.hp,lp:m.teamLp}); nextSample=b.time+10;
    for(const u of b.allyUnits) if(u.kind==='op'){
      const prev=metrics.maxStats[u.defId]||{atk:0,aspd:0};
      metrics.maxStats[u.defId]={atk:Math.max(prev.atk,u.s.atk),aspd:Math.max(prev.aspd,u.s.aspd)};
    }
  }
};
assert(h.run(()=>b.finished,{maxSteps:1000000}),'battle did not finish');
const result=b.result();
assert.equal(result.errors,0); assert.equal(m.errorCount,0); assert.equal(h.logs.error.length,0);
assert(!result.synthetic);
const output={...setup,waves:undefined,roster:undefined,playerEffects:undefined,bonds:undefined,
  completed:true,bossPoolInitial:3000000,bossPoolLeft:m.bossPool.hp,r14BossKilled:m.bossPool.hp===0,
  naturalFullRun:false,goalCompleted:false,time:result.time,reason:result.reason,teamLp:m.teamLp,
  overtimeStartReal:m.bossOvertimeStartReal,overtimeLp:m.overtimeApplied,metrics,result,
  poolByPlayer:Object.fromEntries(m.bossPool.byPlayer),errors:h.logs.error,maxRssKiB:process.resourceUsage().maxRSS};
console.log(JSON.stringify(output));
m.dispose();h.sched.dispose();
