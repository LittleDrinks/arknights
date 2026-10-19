// Use an isolated host: this creates a room and deliberately leaves the test match.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = process.env.ARK_MULTIPLAYER_URL;
const proof = path.resolve(process.env.ARK_MULTIPLAYER_PROOF || 'results/tests/multiplayer-browser');

async function clickText(page, text) {
  await page.waitForFunction(text => [...document.querySelectorAll('button')]
    .some(b => b.textContent.trim() === text && !b.disabled), {}, text);
  await page.evaluate(text => [...document.querySelectorAll('button')]
    .find(b => b.textContent.trim() === text && !b.disabled).click(), text);
}

test('two native browsers: room invitation, ready/start, game action and refresh reconnect without collection',
  { skip: !base, timeout: 180000 }, async t => {
    fs.mkdirSync(proof, { recursive: true });
    const report = { started: new Date().toISOString(), base, browserErrors: [], collectionRequests: [] };
    const browser = await puppeteer.launch({ executablePath: process.env.ARK_CHROME,
      headless: true, pipe: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--enable-unsafe-swiftshader'] });
    t.after(async () => {
      report.finished = new Date().toISOString();
      fs.writeFileSync(path.join(proof, 'report.json'), JSON.stringify(report, null, 2));
      await browser.close();
    });
    const pages = [];
    for (let i = 0; i < 2; i++) {
      const context = await browser.createBrowserContext();
      const page = await context.newPage();
      pages.push(page);
      await page.setViewport({ width: 1440, height: 960 });
      page.on('pageerror', e => report.browserErrors.push({ seat: i, message: e.message }));
      page.on('request', r => { if (r.url().includes('/api/collect')) report.collectionRequests.push(r.url()); });
      // A leftover old collection token must not affect ordinary game networking.
      await page.evaluateOnNewDocument(() => sessionStorage.setItem('ark-collection', JSON.stringify({ collectionId: 'retired', key: 'retired' })));
    }
    const [host, guest] = pages;
    const health = await (await fetch(base + '/healthz')).json();
    assert.equal(health.collect.supported, false);
    assert.equal(health.evaluations, false);
    const old = await fetch(base + '/collect?id=retired', { redirect: 'manual' });
    assert.equal(old.status, 302); assert.equal(old.headers.get('location'), '/');
    assert.equal((await fetch(base + '/collect/app.mjs')).status, 404);
    assert.equal(await (await fetch(base + '/js/net.js')).text(),
      fs.readFileSync(new URL('../game/public/js/net.js', import.meta.url), 'utf8'));
    const enter = async (page, url, name) => {
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('.title-login input');
      assert.equal(await page.$('#consent'), null);
      await page.type('.title-login input', name);
      await clickText(page, '开始');
      await page.waitForFunction(() => globalThis.__SP__?.net.status === 'online');
    };
    try {
      await enter(host, base + '/', '联机测试甲');
      await clickText(host, '创建同盟');
      await host.waitForFunction(() => !!__SP__.store.get().room?.code);
      const room = await host.evaluate(() => __SP__.store.get().room.code);
      report.room = room;
      await enter(guest, base + '/?room=' + room, '联机测试乙');
      await guest.waitForFunction(room => __SP__.store.get().room?.code === room, {}, room);
      const identities = await Promise.all(pages.map(p => p.evaluate(() => __SP__.net.playerId)));
      assert.notEqual(identities[0], identities[1]);
      report.distinctIdentities = true;
      await clickText(guest, '准备就绪');
      await host.screenshot({ path: path.join(proof, 'two-player-room.png'), fullPage: true });
      await clickText(host, '开始模拟');
      await Promise.all(pages.map(p => p.waitForFunction(() => __SP__.store.get().match.public?.phase === 'INFO_CHECK')));
      await Promise.all(pages.map(p => clickText(p, '准备就绪')));
      await Promise.all(pages.map(p => p.waitForFunction(() => __SP__.store.get().match.public?.phase === 'BAND_DRAFT')));
      // The native draft is sequential and a strategy already picked by a teammate is unavailable.
      for (let turn = 0; turn < 2; turn++) {
        const active = await Promise.any(pages.map(async p => {
          await p.waitForFunction(() => [...document.querySelectorAll('button')]
            .some(b => b.textContent.trim() === '确认选择' && !b.disabled));
          return p;
        }));
        await clickText(active, '确认选择');
      }
      await Promise.all(pages.map(p => p.waitForFunction(() => __SP__.store.get().match.public?.phase === 'PREP')));
      report.initialPreparation = await host.evaluate(() => ({ phase: __SP__.store.get().match.public.phase,
        round: __SP__.store.get().match.public.round, room: __SP__.store.get().room.code }));
      await guest.reload({ waitUntil: 'domcontentloaded' });
      await guest.waitForFunction(() => globalThis.__SP__?.net.status === 'online' &&
        __SP__.store.get().match.public?.phase === 'PREP');
      assert.equal(await guest.evaluate(() => __SP__.net.playerId), identities[1]);
      assert.equal(await guest.evaluate(() => __SP__.store.get().room.code), room);
      report.refreshReconnectedSameSeat = true;
      await host.screenshot({ path: path.join(proof, 'native-game.png'), fullPage: true });
      await Promise.all(pages.map(p => p.evaluate(() => __SP__.net.request('g.ready', { ready: true }))));
      await Promise.all(pages.map(p => p.waitForFunction(() => __SP__.store.get().match.public?.phase === 'COMBAT')));
      await host.screenshot({ path: path.join(proof, 'native-combat.png'), fullPage: true });
      await Promise.all(pages.map(p => p.waitForFunction(() =>
        __SP__.store.get().match.public?.round >= 2, { timeout: 90000 })));
      report.firstCombatSettled = true;
      report.afterCombat = await Promise.all(pages.map(p => p.evaluate(() => ({
        round: __SP__.store.get().match.public.round, phase: __SP__.store.get().match.public.phase,
        combatMode: __SP__.store.get().match.public.combatMode,
      }))));
      assert.deepEqual(report.collectionRequests, []);
      assert.deepEqual(report.browserErrors, []);
      report.pass = true;
    } catch (error) {
      report.failure = error.stack;
      await Promise.allSettled(pages.map((p, i) => p.screenshot({ path: path.join(proof, `failure-${i}.png`), fullPage: true })));
      throw error;
    } finally {
      await Promise.allSettled(pages.map(p => p.evaluate(async () => {
        if (globalThis.__SP__?.store.get().match.public) await __SP__.net.request('g.leave');
        if (globalThis.__SP__?.store.get().room) await __SP__.net.request('room.leave');
      })));
    }
  });
