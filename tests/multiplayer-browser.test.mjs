// Isolated host only: these automated games are transport/capture evidence, not expert data.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { validateRecordings } from '../arknights/replay/export.mjs';
const base = process.env.ARK_MULTIPLAYER_URL;
const proofRoot = path.resolve(process.env.ARK_MULTIPLAYER_PROOF || 'results/tests/multiplayer-browser');
const matrix = process.env.ARK_MULTIPLAYER_HUMANS ? [[Number(process.env.ARK_MULTIPLAYER_HUMANS), Number(process.env.ARK_MULTIPLAYER_BOTS || 0)]] : [[1,3],[2,2],[3,1],[4,0]];
async function clickText(page, text) {
  await page.waitForFunction(text => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === text && !b.disabled), {}, text);
  await page.evaluate(text => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === text && !b.disabled).click(), text);
}
for (const [humans, bots] of matrix) test(`${humans} human + ${bots} AI: automatic capture, reconnect, battle, annotation/export and native replay`, {skip: !base, timeout: 240000}, async t => {
  const proof = path.join(proofRoot, `${humans}h-${bots}ai`); fs.mkdirSync(proof, {recursive:true});
  const report = {started:new Date().toISOString(),humans,bots,browserErrors:[]};
  const browser = await puppeteer.launch({executablePath:process.env.ARK_CHROME,headless:true,pipe:true,args:['--no-sandbox','--disable-dev-shm-usage','--enable-unsafe-swiftshader']});
  const pages=[];
  t.after(async()=>{report.finished=new Date().toISOString();fs.writeFileSync(path.join(proof,'report.json'),JSON.stringify(report,null,2));await browser.close();});
  for(let i=0;i<humans;i++) {
    const ctx=await browser.createBrowserContext(),p=await ctx.newPage();pages.push(p);await p.setViewport({width:1440,height:960});
    p.on('pageerror',e=>report.browserErrors.push({seat:i,message:e.message}));
  }
  const host=pages[0],guest=pages.at(-1);
  const api=async(id,suffix='',body)=>host.evaluate(async(id,suffix,body)=>{
    const p=JSON.parse(localStorage.getItem('ark-participant:'+id)),organizer=localStorage.getItem('ark-organizer:'+id);
    const r=await fetch('/api/collect/'+id+suffix,{method:body===undefined?'GET':'POST',headers:{'X-Collect-Participant':p.key,...(organizer?{'X-Collect-Organizer':organizer}:{}),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const value=await r.json();if(!r.ok)throw Error(value.error);return value;
  },id,suffix,body);
  const enter=async(p,url,name)=>{await p.goto(url,{waitUntil:'domcontentloaded'});await p.waitForSelector('.title-login input');assert.equal(await p.$('#consent'),null);await p.type('.title-login input',name);await clickText(p,'开始');await p.waitForFunction(()=>globalThis.__SP__?.net.status==='online');};
  try {
    const health=await(await fetch(base+'/healthz')).json();assert.equal(health.collect.automatic,true);assert.equal(health.evaluations,false);
    assert.equal((await fetch(base+'/collect/app.mjs')).status,404);
    await enter(host,base+'/','采集验收1');await clickText(host,'创建同盟');await host.waitForFunction(()=>!!__SP__.store.get().room?.code);
    const room=await host.evaluate(()=>__SP__.store.get().room.code);report.room=room;
    for(let i=1;i<humans;i++){await enter(pages[i],base+'/?room='+room,'采集验收'+(i+1));await pages[i].waitForFunction(room=>__SP__.store.get().room?.code===room,{},room);}
    for(let i=0;i<bots;i++)await host.evaluate(()=>__SP__.net.request('room.addBot',{}));
    const credentials=await Promise.all(pages.map(p=>p.evaluate(()=>JSON.parse(sessionStorage.getItem('ark-collection')))));
    const id=credentials[0].collectionId;report.collectionId=id;assert(credentials.every(p=>p.collectionId===id));assert.equal(new Set(credentials.map(p=>p.key)).size,humans);
    const created=await api(id);assert.equal(created.automatic,true);assert.equal(created.matches.length,0);assert.equal(created.roomCode,room);
    for(const p of pages.slice(1))await clickText(p,'准备就绪');
    await host.screenshot({path:path.join(proof,'room.png'),fullPage:true});await clickText(host,'开始模拟');
    await Promise.all(pages.map(p=>p.waitForFunction(()=>__SP__.store.get().match.public?.phase==='INFO_CHECK')));
    const started=await api(id),match=started.matches[0];report.matchId=match.id;
    assert.equal(match.seats.length,humans+bots);assert.equal(match.seats.filter(s=>s.isBot).length,bots);assert.equal(match.seats.filter(s=>s.participantId).length,humans);
    await Promise.all(pages.map(p=>clickText(p,'准备就绪')));
    await Promise.all(pages.map(p=>p.waitForFunction(()=>__SP__.store.get().match.public?.phase==='BAND_DRAFT')));
    for(let turn=0;turn<humans;turn++) {
      const active=await Promise.any(pages.map(async p=>{await p.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='确认选择'&&!b.disabled));return p;}));await clickText(active,'确认选择');
    }
    await Promise.all(pages.map(p=>p.waitForFunction(()=>__SP__.store.get().match.public?.phase==='PREP')));
    const playerId=await guest.evaluate(()=>__SP__.net.playerId);await guest.reload({waitUntil:'domcontentloaded'});
    await guest.waitForFunction(()=>globalThis.__SP__?.net.status==='online'&&__SP__.store.get().match.public?.phase==='PREP');assert.equal(await guest.evaluate(()=>__SP__.net.playerId),playerId);
    assert.equal((await guest.evaluate(()=>JSON.parse(sessionStorage.getItem('ark-collection')))).collectionId,id);report.reconnect=true;
    await Promise.all(pages.map(p=>p.evaluate(()=>__SP__.net.request('g.refresh'))));
    await Promise.all(pages.map(p=>p.evaluate(()=>__SP__.net.request('g.sell',{uid:2147483647}).then(()=>{throw Error('unowned sell unexpectedly accepted');},e=>({rejected:e.code})))));
    await Promise.all(pages.map(p=>p.evaluate(()=>__SP__.net.request('g.ready',{ready:true}))));
    await Promise.all(pages.map(p=>p.waitForFunction(()=>__SP__.store.get().match.public?.phase==='COMBAT')));
    await host.screenshot({path:path.join(proof,'combat.png'),fullPage:true});
    await Promise.all(pages.map(p=>p.waitForFunction(()=>__SP__.store.get().match.public?.round>=2,{timeout:90000})));
    assert.equal(await host.evaluate(()=>__SP__.store.get().match.public.combatMode),'server');report.firstCombatSettled=true;
    const battles=await api(id,`/battles?match=${match.id}&seat=0`);assert(battles.length>0);
    const rec=await api(id,'/battle?name='+battles[0].name);validateRecordings([rec]);assert(rec.frames.length>0);report.battleFrames=rec.frames.length;
    for(const s of match.seats.filter(s=>!s.isBot)) {
      const rows=await api(id,`/timeline?match=${match.id}&seat=${s.seat}`);assert(rows.some(r=>r.kind==='request'&&r.message.t==='g.refresh'));assert(rows.some(r=>r.kind==='ack'&&r.status==='accepted'));assert(rows.some(r=>r.kind==='ack'&&r.status==='rejected'));assert(rows.some(r=>r.kind==='frame'&&r.message.t==='m.private'));assert(rows.some(r=>r.kind==='frame'&&r.message.t==='m.public'));
    }
    report.allHumanSeatsRecorded=true;
    await Promise.all(pages.map(p=>p.evaluate(()=>__SP__.net.request('g.leave'))));
    await host.waitForFunction(async id=>{const key=localStorage.getItem('ark-organizer:'+id);const c=await(await fetch('/api/collect/'+id,{headers:{'X-Collect-Organizer':key}})).json();return c.matches[0].status!=='recording';},{timeout:15000},id);
    const sealed=await api(id);assert.notEqual(sealed.matches[0].status,'technical-failure');report.status=sealed.matches[0].status;
    const exported=await api(id,'/export',{profile:'all-human'});assert(exported.quality.samples>=humans);report.confirmedSamples=exported.quality.samples;
    await host.goto(base+'/collect',{waitUntil:'domcontentloaded'});await host.waitForSelector('#rooms section button');await host.click('#rooms section button');await host.waitForSelector('#start option');
    await host.type('#core','验收');await host.click('#annotation button');await host.waitForFunction(()=>document.getElementById('feedback').textContent==='标注已保存');
    report.annotation=true;await host.click('#replay');await host.waitForFunction(()=>globalThis.__collectReplay?.ready,{timeout:60000});
    const option=await host.$eval('#battles',s=>s.options[1]?.value);assert(option);await host.select('#battles',option);await host.waitForFunction(()=>__collectReplay.battleReady,{timeout:60000});
    await host.click('#play');await host.screenshot({path:path.join(proof,'replay.png'),fullPage:true});report.nativeReplay=true;
    assert.deepEqual(report.browserErrors,[]);report.pass=true;
  }catch(e){report.failure=e.stack;await Promise.allSettled(pages.map((p,i)=>p.screenshot({path:path.join(proof,`failure-${i}.png`),fullPage:true})));throw e;}
  finally {await Promise.allSettled(pages.map(p=>p.evaluate(async()=>{if(globalThis.__SP__?.store.get().match.public)await __SP__.net.request('g.leave');if(globalThis.__SP__?.store.get().room)await __SP__.net.request('room.leave');})));}
});
