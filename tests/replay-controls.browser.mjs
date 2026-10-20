// PUPPETEER_EXECUTABLE_PATH=/path/to/chrome node tests/replay-controls.browser.mjs http://host:port replay-id
import assert from 'node:assert/strict';
import fs from 'node:fs';
import puppeteer from 'puppeteer-core';

const origin = process.argv[2] || 'http://127.0.0.1:38181';
const id = process.argv[3] || 'bundle-ec965d25-3e0e-49c8-a363-abb39532b0c7';
const browser = await puppeteer.launch({ executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
  headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--enable-unsafe-swiftshader'] });
try {
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const cdp = await page.createCDPSession(); let sockets = 0;
  await cdp.send('Network.enable'); cdp.on('Network.webSocketCreated', () => sockets++);
  await page.setViewport({ width: 1280, height: 720 });
  await page.goto(`${origin}/?replay=${encodeURIComponent(id)}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => globalThis.__nativeReplay?.state.steps.length > 0
    && !document.querySelector('[data-control=play]')?.disabled, { timeout: 60000 });
  const layouts = [];
  for (const [width, height] of [[1280, 720], [1920, 1080], [800, 480]]) {
    await page.setViewport({ width, height });
    const visible = await page.evaluate(() => {
      const button = document.querySelector('[data-control=play]'), r = button.getBoundingClientRect();
      const dock = document.querySelector('.agent-replay-dock');
      return r.width > 0 && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight
        && button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2))
        && getComputedStyle(dock).position === 'fixed';
    });
    assert.equal(visible, true, `Play control must be visible and clickable at ${width}x${height}`);
    layouts.push({ width, height, visible });
  }
  await page.setViewport({ width: 1280, height: 720 });
  const before = await page.evaluate(() => globalThis.__nativeReplay.state.index);
  await page.click('[data-control=play]');
  await page.waitForFunction(index => globalThis.__nativeReplay.state.index > index, {}, before);
  await page.click('[data-control=play]');
  assert.equal(await page.evaluate(() => globalThis.__nativeReplay.state.playing), false);
  await page.click('[data-control=battle]');
  await page.waitForFunction(() => globalThis.__nativeReplay.state.mode === 'battle'
    && globalThis.__nativeReplay.state.frameIndex > 3, { timeout: 30000 });
  await page.click('[data-control=play]');
  const playback = await page.evaluate(() => ({ replay: globalThis.__SP__.replay,
    playing: globalThis.__nativeReplay.state.playing, frameIndex: globalThis.__nativeReplay.state.frameIndex,
    status: document.querySelector('[data-control=status]').textContent }));
  assert.equal(playback.replay, true); assert.equal(playback.playing, false);
  // A few initial frames would miss a playback loop that stalls later in a battle.
  await page.select('[data-control=speed]', '4');
  await page.click('[data-control=play]');
  await page.waitForFunction(() => globalThis.__nativeReplay.state.mode === 'actions', { timeout: 30000 });
  await page.click('[data-control=play]');
  const afterBattle = await page.evaluate(() => ({ index: globalThis.__nativeReplay.state.index,
    playing: globalThis.__nativeReplay.state.playing }));
  assert(afterBattle.index > before); assert.equal(afterBattle.playing, false);
  assert.equal(sockets, 0, 'Playback must not connect to a live game');
  assert.deepEqual(errors, []);
  const proof = { pass: true, id, layouts, playback, battleCompleted: true, afterBattle, liveSockets: sockets };
  if (process.env.ARK_BROWSER_PROOF) fs.writeFileSync(process.env.ARK_BROWSER_PROOF, JSON.stringify(proof, null, 2));
  console.log(JSON.stringify(proof));
} finally { await browser.close(); }
