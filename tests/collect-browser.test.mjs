// Run against the ISOLATED real-time viewer, never the canonical active match host.
// ARK_COLLECT_URL=http://127.0.0.1:48191 ARK_CHROME=/path/to/chrome node --test tests/collect-browser.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
const base=process.env.ARK_COLLECT_URL, proof=path.resolve(process.env.ARK_COLLECT_PROOF || 'results/tests/collect-browser');
const organizerKeys=new Map();
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const api=async (route,body)=>{const key=organizerKeys.get(route.split(/[/?]/)[1]);const r=await fetch(base+'/api/collect'+route,{method:body===undefined?'GET':'POST',headers:{...(key?{'X-Collect-Organizer':key}:{}),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});const v=await r.json();if(!r.ok)throw Error(JSON.stringify(v));return v;};
const act=(page,t,fields={})=>page.evaluate(async(t,fields)=>{try{return {ok:true,reply:await __SP__.net.request(t,fields)};}catch(e){return {ok:false,code:e.code,message:e.message};}},t,fields);
const snapshot=page=>page.evaluate(()=>({playerId:__SP__.net.playerId,connection:__SP__.net.status,room:__SP__.store.get().room,match:__SP__.store.get().match}));
async function clickText(page,text){return page.evaluate(text=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===text&&!b.disabled);if(!b)return false;b.click();return true;},text);}

test('four isolated native browser contexts: consent, normal room, manual actions, real WS reconnect, natural result, annotations/export and native recorded replay', {skip:!base,timeout:1800000}, async t=>{
  fs.mkdirSync(proof,{recursive:true});
  const report={source:'automated browser actions through original Net/UI, not expert demonstrations',base,actions:[],browserErrors:[],screenshots:[],started:new Date().toISOString()};
  const browser=await puppeteer.launch({executablePath:process.env.ARK_CHROME,headless:true,pipe:true,timeout:90000,args:['--no-sandbox','--disable-dev-shm-usage','--enable-unsafe-swiftshader']});
  const pages=[];let organizer;
  const shot=async(page,name)=>{await page.bringToFront();await page.screenshot({path:path.join(proof,name+'.png'),fullPage:true,waitForFonts:false,timeout:60000});report.screenshots.push(name+'.png');};
  t.after(async()=>{report.finished=new Date().toISOString();fs.writeFileSync(path.join(proof,'report.json'),JSON.stringify(report,null,2));await browser.close();});
  try {
    const context=await browser.createBrowserContext();organizer=await context.newPage();await organizer.setViewport({width:1440,height:1000});
    await organizer.goto(base+'/collect',{waitUntil:'domcontentloaded'});await organizer.type('#label','Four-browser real capture');await organizer.click('#create-button');
    await organizer.waitForFunction(()=>new URLSearchParams(location.search).has('id'));const id=new URL(organizer.url()).searchParams.get('id');report.collectionId=id;organizerKeys.set(id,await organizer.evaluate(id=>localStorage.getItem('ark-organizer:'+id),id));
    for(let i=0;i<4;i++) {
      const ctx=await browser.createBrowserContext(),page=await ctx.newPage();pages.push(page);await page.setViewport({width:1440,height:1000});
      page.on('pageerror',e=>report.browserErrors.push({seat:i,message:e.message}));
      await page.goto(base+'/collect?id='+id,{waitUntil:'domcontentloaded'});
      await page.waitForSelector('#detail:not([hidden])');await page.click('#consent');await page.click('#join');
      await page.waitForFunction(()=>!!globalThis.__SP__,{timeout:60000});
      await page.waitForSelector('.title-login input');await page.type('.title-login input',`CollectSeat${i+1}`);assert(await clickText(page,'开始'));
      await page.waitForFunction(()=>__SP__.net.status==='online');
    }
    report.identities=await Promise.all(pages.map(snapshot));assert.equal(new Set(report.identities.map(s=>s.playerId)).size,4);
    assert((await act(pages[0],'room.create',{mode:'coop',difficulty:'ABYSS'})).ok);
    const room=(await snapshot(pages[0])).room.code;report.room=room;
    for(let i=1;i<4;i++)assert((await act(pages[i],'room.join',{code:room})).ok);
    for(let i=1;i<4;i++)assert((await act(pages[i],'room.ready',{ready:true})).ok);
    await delay(250);await shot(pages[0],'four-seat-room');
    assert(await clickText(pages[0],'开始模拟'));
    await pages[0].waitForFunction(()=>!!__SP__.store.get().match.public);
    const first=await snapshot(pages[0]);assert.equal(first.match.public.combatMode,'server');
    report.initial=first;
    const invalid=await act(pages[0],'g.infoReady',{setupRevision:first.match.public.setupRevision+1});assert.equal(invalid.ok,false);report.staleRevision=invalid;
    for(const page of pages)assert((await act(page,'g.infoReady',{setupRevision:first.match.public.setupRevision})).ok);
    const done=new Set(), covered=new Set();let reconnect=false,battleShot=false,lastPhase='',lastProgress=Date.now();
    for(;;) {
      const snaps=await Promise.all(pages.map(snapshot)), pub=snaps[0].match.public;
      if(!pub){await delay(300);continue;}
      const phaseKey=pub.phase+':'+pub.round;
      if(phaseKey!==lastPhase){console.log('real match',phaseKey);lastPhase=phaseKey;lastProgress=Date.now();}
      assert(Date.now()-lastProgress<240000,'real match stopped making phase progress');
      if(pub.phase==='RESULT'||snaps[0].match.result){report.terminal=snaps.map(s=>s.match.result);break;}
      for(let i=0;i<4;i++) {
        const s=snaps[i],own=s.match.private,p=s.match.public,page=pages[i];if(!own||!p)continue;
        const record=async(t,fields)=>{const r=await act(page,t,fields);report.actions.push({seat:i,round:p.round,t,fields,...r});if(r.ok)covered.add(t);return r;};
        if(p.phase==='BAND_DRAFT'&&p.draft?.turn===s.playerId&&!p.draft.picks[s.playerId]) {
          const band=['band_orchid','band_amiya','band_duyaoy','band_bldsk'].find(b=>!Object.values(p.draft.picks).includes(b));await record('g.band',{bandId:band});
        } else if(p.phase==='SP_DRAFT'&&p.sp?.turn===s.playerId) {
          const idx=p.sp.cards.findIndex((c,n)=>!c.soldOut&&!p.sp.taken[n]);if(idx>=0)await record('g.choice',{idx});
        } else if(p.phase==='PREP'&&own.alive&&!own.ready) {
          const key=i+':'+p.round;
          if(own.personalChoice){await record('g.choice',{idx:0,choiceId:own.personalChoice.id});continue;}
          if(!done.has(key)) {
            done.add(key);
            if(!reconnect&&i===1) {
              reconnect=true;const before=own.playerId;
              // Leave schema-invalid and lobby-only operations immediately before a REAL page reload/rid reset.
              await page.evaluate(()=>{for(const rid of [6,7,8,9])__SP__.net._sendRaw({t:'g.buy',slot:'invalid',rid});});
              await act(page,'room.loadout',{entries:{}});
              await page.reload({waitUntil:'domcontentloaded'});await page.waitForFunction(()=>!!globalThis.__SP__,{timeout:60000});
              await page.waitForFunction(()=>__SP__.net.status==='online'&&!!__SP__.store.get().match.private);
              assert.equal((await snapshot(page)).playerId,before);report.reconnect={playerId:before,passed:true,mode:'full page reload resets rid with prior invalid/lobby-only requests'};
            }
            if(i===0&&p.round===1) {
              const bad=await record('g.buy',{slot:99});assert(!bad.ok);
              // Actual original shop click path (two taps arm, then buy), in addition to parameterized manual actions below.
              const slot=own.shop.slots.findIndex(s=>s&&!s.sold&&s.kind==='chess'&&s.price<=own.funds);
              if(slot>=0){const selector=`.scard[data-shop-slot="${slot}"]`;if(await page.$(selector)){await page.click(selector);await page.click(selector);await delay(100);report.shopUiClick=true;}}
            }
            // Buy real available pieces/equipment, preserving exact UID/target parameters.
            for(let n=0;n<3;n++) {
              const v=(await snapshot(page)).match.private;
              const slot=v.shop.slots.findIndex(s=>s&&!s.sold&&s.price<=v.funds&&v.hand.includes(null));
              if(slot<0)break;await record('g.buy',{slot});
            }
            let v=(await snapshot(page)).match.private;
            const piece=[...v.hand,...v.board].find(x=>x?.kind==='chess');
            if(piece&&(i===0||i===1)) {
              let moved=false;
              for(const row of [11,10,9]){for(const col of [4,6,8,2]){if((await record('g.move',{uid:piece.uid,to:{area:'board',row,col},dir:'UP'})).ok){moved=true;break;}}if(moved)break;}
              if(moved){await record('g.move',{uid:piece.uid,to:{area:'board',row:(await snapshot(page)).match.private.board.find(p=>p.uid===piece.uid).row,col:(await snapshot(page)).match.private.board.find(p=>p.uid===piece.uid).col},dir:'LEFT'});}
            }
            v=(await snapshot(page)).match.private;
            const item=[...v.hand,...v.temp].find(x=>x?.kind==='item');
            const chess=[...v.board,...v.hand].find(x=>x?.kind==='chess');
            if(item&&chess)await record('g.equip',{itemUid:item.uid,targetUid:chess.uid,replaceUid:null});
            // Preserve a tactical loss: after verifying placement, bench all deployed operators normally.
            v=(await snapshot(page)).match.private;
            for(const piece of v.board){const idx=(await snapshot(page)).match.private.hand.indexOf(null);if(idx>=0)await record('g.move',{uid:piece.uid,to:{area:'hand',idx},dir:'LEFT'});}
          }
          const latest=(await snapshot(page)).match.private;
          if(latest.shop.rewardOffer){const idx=latest.shop.rewardOffer.slots.findIndex(x=>!x.sold);if(idx>=0)await record('g.reward',{idx});}
          else if(latest.canReady)await record('g.ready',{ready:true});
        }
      }
      if(pub.phase==='COMBAT'&&!battleShot){battleShot=true;await shot(pages[0],'live-native-combat');}
      await delay(350);
    }
    await shot(pages[0],'actual-result');
    const collection=await api('/'+id),match=collection.matches[0];report.match=match;
    assert.equal(match.status,'complete');assert.equal(match.complete,true);assert.equal(match.victory,false);
    assert(report.terminal.every(r=>r&&r.victory===false));
    const anonymous = await (await fetch(base+'/api/collect/'+id)).json();assert.deepEqual(anonymous.matches,[]);
    const publicList=await(await fetch(base+'/api/collect')).json();assert.deepEqual(publicList,[]);
    const other=(await api(`/${id}/battles?match=${match.id}&seat=0`))[0];
    assert.equal((await fetch(`${base}/api/collect/${id}/timeline?match=${match.id}&seat=1`)).status,403);
    assert.equal((await fetch(`${base}/api/collect/${id}/battle?name=${other.name}`)).status,403);
    report.capabilityPrivacy={anonymousListEmpty:true,anonymousPrivateDenied:true};
    const rows=await api(`/${id}/timeline?match=${match.id}&seat=0`),requests=rows.filter(r=>r.kind==='request');
    assert(requests.some(r=>r.message.t==='g.move'&&r.message.to));
    assert(requests.every(r=>r.decision&&r.decision.decisionSeq<=r.flushSeq));
    report.coveredActions=[...covered];report.actionCounts=Object.fromEntries([...covered].map(t=>[t,report.actions.filter(a=>a.t===t&&a.ok).length]));
    const annotation=await api('/'+id+'/annotations',{matchId:match.id,seat:0,start:rows[0].eventId,end:rows.at(-1).eventId,goalCore:'manual-coverage',goalSupport:'',stage:'prep',quality:'uncertain',note:'automated browser protocol proof'});
    await api('/'+id+'/annotations',{...annotation,quality:'usable',note:'revised after real terminal evidence'});
    const ref=await api('/'+id+'/export',{profile:'human-reviewed'});report.dataset=ref;assert(ref.quality.samples>0);
    const all=await api('/'+id+'/export',{profile:'all-human'});assert(all.quality.actionCoverage['g.equip'].parameterized>0);report.allHumanQuality=all.quality;
    const dataText=await(await fetch(`${base}/api/collect/${id}/dataset?version=${all.version}`,{headers:{'X-Collect-Organizer':organizerKeys.get(id)}})).text();
    const samples=dataText.trim().split('\n').map(JSON.parse);
    assert(samples.some(s=>s.seat===1&&s.action.t==='g.move'),'post-reload accepted move has matching execution and decision history');
    report.postReloadSamples=samples.filter(s=>s.seat===1).reduce((counts,s)=>(counts[s.action.t]=(counts[s.action.t]||0)+1,counts),{});
    await organizer.reload({waitUntil:'domcontentloaded'});
    await organizer.waitForSelector('#matches button');await organizer.click('#matches button');await organizer.waitForSelector('#annotation:not([hidden])');
    await organizer.click('#revisions button:last-child');await organizer.$eval('#note',n=>{n.value='UI annotation revision proof';});await organizer.click('#annotation button');
    await organizer.waitForFunction(()=>document.getElementById('feedback').textContent.includes('保存成功'));
    assert.equal(await organizer.$eval('#dataset-details',n=>n.open),false);await shot(organizer,'collection-detail');
    // A guest must retain access by clicking the real own-seat replay anchor, with NO organizer key.
    const guest=pages[1];await guest.goto(base+'/collect?id='+id,{waitUntil:'domcontentloaded'});
    await guest.waitForSelector('#matches button');assert.equal(await guest.evaluate(id=>localStorage.getItem('ark-organizer:'+id),id),null);
    await guest.click('#matches button');await guest.waitForSelector('#timeline a');assert.equal(await guest.$eval('#timeline a',a=>a.target),'');
    const denied=await guest.evaluate(async(id,matchId)=>{const p=JSON.parse(sessionStorage.getItem('ark-collection'));return (await fetch(`/api/collect/${id}/timeline?match=${matchId}&seat=0`,{headers:{'X-Collect-Participant':p.key}})).status;},id,match.id);assert.equal(denied,403);
    await guest.click('#timeline a');await guest.waitForFunction(()=>globalThis.__collectReplay?.ready,{timeout:60000});
    assert.equal(await guest.evaluate(()=>__collectReplay.seat),1);await shot(guest,'guest-own-seat-replay');
    report.guestReplay={actualAnchor:true,sameTab:true,ready:true,otherSeatDenied:true};
    // Sealed expert data cannot be invalidated by later ordinary play from the same identity.
    await guest.evaluate(()=>sessionStorage.removeItem('ark-collection'));await guest.goto(base+'/',{waitUntil:'domcontentloaded'});
    await guest.waitForFunction(()=>globalThis.__SP__?.net.status==='online',{timeout:60000});
    const afterResume=await api('/'+id+'/export',{profile:'all-human'});assert.equal(afterResume.quality.samples,all.quality.samples);report.sealedUninstrumentedResume=true;
    const replay=await context.newPage();await replay.setViewport({width:1440,height:1000});
    replay.on('pageerror',e=>report.browserErrors.push({replay:true,message:e.message}));
    await replay.goto(`${base}/collect/replay?id=${id}&match=${match.id}&seat=0`,{waitUntil:'domcontentloaded'});
    await replay.waitForFunction(()=>globalThis.__collectReplay?.ready,{timeout:60000});
    const option=await replay.$eval('#battles',s=>s.options[1]?.value);assert(option,'real recorded battles exist');
    await replay.select('#battles',option);await replay.waitForFunction(()=>__collectReplay.battleReady,{timeout:60000});
    await replay.waitForSelector('canvas',{timeout:60000});await replay.click('#play');await delay(1500);
    report.nativeReplay=await replay.evaluate(()=>({state:__collectReplay,canvas:document.querySelectorAll('canvas').length,status:document.getElementById('status').textContent}));
    await shot(replay,'native-recorded-replay');assert.equal(report.nativeReplay.state.replayMode,'recorded-events');
    report.pass=true;
  } catch(e) {report.failure=e.stack;if(organizer)await shot(organizer,'failure-organizer');for(let i=0;i<pages.length;i++)await shot(pages[i],'failure-seat-'+i);throw e;}
});
