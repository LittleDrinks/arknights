// Run against a real preview: PUPPETEER_EXECUTABLE_PATH=/path/to/chrome node tests/replay-boot.browser.mjs http://localhost:38181
// npm ci supplies puppeteer-core; ARK_BROWSER_PACKAGE_ROOT may point at an isolated dependency package.json.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require = createRequire(path.resolve(process.env.ARK_BROWSER_PACKAGE_ROOT || 'package.json'));
const { default: puppeteer } = require('puppeteer-core');
const origin = process.argv[2] || 'http://127.0.0.1:38181';
const browser = await puppeteer.launch({ executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
  headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const proof = [];
try {
  const index = await (await fetch(origin + '/dev/agent/index.json')).json();
  const target = index.episodes.find(e => e.id === 'solo-ABYSS-s1') || index.episodes[0];
  assert(target);
  for (const scenario of ['missing-id', 'missing-file', 'index-outage']) {
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let fail = true;
    await page.setRequestInterception(true);
    page.on('request', req => {
      const pathname = new URL(req.url()).pathname;
      if (pathname === '/dev/agent/index.json') {
        if (scenario === 'index-outage' && fail) req.respond({ status: 503, body: 'temporarily unavailable' });
        else req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...index, episodes: [target] }) });
      } else if (pathname === '/dev/agent/' + target.file && scenario === 'missing-file' && fail) {
        req.respond({ status: 404, body: 'not found' });
      } else req.continue();
    });
    await page.goto(origin + '/?replay=' + (scenario === 'missing-id' ? 'missing-replay-probe' : target.id));
    await page.waitForFunction(() => document.querySelector('[data-control=retry]') || document.querySelector('#boot-err')?.textContent.includes('启动失败'), { timeout: 30000 });
    assert.equal(await page.$('#boot'), null, scenario + ': playback errors must not fail app startup');
    assert.match(await page.$eval('[data-control=status]', e => e.textContent), /回放载入失败/);
    assert.equal(await page.$eval('[data-control=play]', e => e.disabled), true);
    fail = false;
    if (scenario === 'missing-id') await page.select('[data-control=episode]', target.id);
    else await page.click('[data-control=retry]');
    await page.waitForFunction(() => document.querySelectorAll('[data-control=round] option').length > 0 && !document.querySelector('[data-control=play]').disabled, { timeout: 30000 });
    assert.equal(errors.length, 0, errors.join('\n'));
    proof.push({ scenario, recoveredEpisode: await page.$eval('[data-control=episode]', e => e.value), status: await page.$eval('[data-control=status]', e => e.textContent) });
    await page.close();
  }
  console.log(JSON.stringify({ pass: true, proof }));
  if (process.env.ARK_BROWSER_PROOF) fs.writeFileSync(process.env.ARK_BROWSER_PROOF, JSON.stringify({ pass: true, proof }, null, 2));
} finally { await browser.close(); }
