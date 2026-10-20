// Exercises the actual field renderer: advancing replay controls alone cannot detect a fallback board.
// PUPPETEER_EXECUTABLE_PATH=/path/to/chrome node tests/field-loading.browser.mjs http://host:port
import assert from 'node:assert/strict';
import fs from 'node:fs';
import puppeteer from 'puppeteer-core';
const origin = process.argv[2] || 'http://127.0.0.1:38181';
const id = process.argv[3] || 'bundle-ec965d25-3e0e-49c8-a363-abb39532b0c7';
const browser = await puppeteer.launch({ executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
  headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--enable-unsafe-swiftshader'] });
const proof = { origin, id, runs: [] };
try {
  const page = await browser.newPage(); await page.setViewport({ width: 1280, height: 900 });
  const errors = [], fallbacks = [];
  proof.errors = errors; proof.fallbacks = fallbacks;
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.text().includes('[field]')) fallbacks.push(msg.text()); });
  for (const cache of ['cold', 'warm']) {
    const started = Date.now();
    await page.goto(`${origin}/?replay=${encodeURIComponent(id)}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => globalThis.__SP_VIEW__?.kind === 'engine'
      && !document.querySelector('.gm__loading') && !!document.querySelector('canvas[aria-label]'), { timeout: 60000 });
    const run = { cache, fieldReadyMs: Date.now() - started, ...await page.evaluate(() => ({
      kind: globalThis.__SP_VIEW__?.kind, stats: globalThis.__SP_VIEW__?.raw?.stats?.(),
      scripts: performance.getEntriesByType('resource').filter(r => /\.(m?js)(\?|$)/.test(r.name))
        .map(r => ({ path: new URL(r.name).pathname, bytes: r.transferSize, duration: r.duration })),
    })) };
    proof.runs.push(run); console.log(JSON.stringify({ cache, fieldReadyMs: run.fieldReadyMs, kind: run.kind, scripts: run.scripts.length }));
    assert.equal(fallbacks.length, 0, fallbacks.join('\n'));
    assert(!run.scripts.some(s => s.path === '/sim/spec.js'), 'Recorded playback must not warm the unused local simulator');
    // Budget applies to the whole navigation, including the module graph and recorded state.
    assert(run.fieldReadyMs < Number(process.env.ARK_FIELD_BUDGET_MS || 15000), `Field cold/warm load too slow: ${run.fieldReadyMs}ms`);
  }
  assert.deepEqual(errors, []);
  proof.pass = true;
} finally {
  if (process.env.ARK_BROWSER_PROOF) fs.writeFileSync(process.env.ARK_BROWSER_PROOF, JSON.stringify(proof, null, 2));
  await browser.close();
}
