// Constructed scenarios using the original real Match, PlayerState, registry and data. NOT natural victories.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { runStacking, runStackingSteps } from './stacking.mjs';
import { BOT_SHA256, BEGIN_ANCHOR, transformStackingBot, load } from './stacking-loader.mjs';
import { createHash } from 'node:crypto';
import { environmentOptions, stackingPrepSteps, stackingReports } from './stacking-runtime.mjs';

const root = resolve(process.env.ARK_STACKING_GAME_ROOT ?? fileURLToPath(new URL('../../Stronghold-Protocol/', import.meta.url)));
assert.equal(execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(), '6ab2468279e926d107788f63d7fe219b85f67b4a');
execFileSync('git', ['diff', '--exit-code', 'HEAD', '--', 'server', 'shared', 'data', 'test/match'], { cwd: root, stdio: 'pipe' });
const { makeMatch, give, legalTileFor, DATA } = await import(pathToFileURL(resolve(root, 'test/match/harness.js')));
const id = name => Object.values(DATA.chess).find(c => c.name === name && c.visible && !c.isGolden).chessId;
const refreshTeam = ['拉普兰德', '阿罗玛', '安洁莉娜', '莫斯提马'];
const acquireTeam = ['夕', '小满', '诗怀雅', '阿罗玛'];
function fixture(t, names = refreshTeam) {
  const h = makeMatch({ mode: 'solo', difficulty: 'ABYSS', seed: 1, captureFrames: false });
  t.after(() => h.m.dispose());
  h.start(); h.toPrep(1, { band: 'band_sarkazb' });
  const ps = h.ps('p_0'), m = h.m;
  for (const name of names) {
    const tile = legalTileFor(m, ps, id(name));
    assert.ok(tile); give(m, ps, id(name), 'board', tile);
  }
  // Setup only. The macro itself never changes funds, counters, offers or layers directly.
  ps.funds = 20; ps.shop.freeRefreshes = 0;
  ps.shop.slots.fill(null);
  return { h, m, ps };
}
function offer(ps, chessId, slot = 0, price = 2) {
  ps.shop.slots[slot] = { kind: 'chess', id: chessId, basePrice: price, sold: false, frozen: false };
}
function check(h) { h.invariants(); assert.deepEqual(h.logs.error, []); }
const cheap = 'chess_char_1_07_a';

test('refresh: reads prior history, pays only two missing refreshes, then real prep-end cap', t => {
  const { h, m, ps } = fixture(t);
  assert.equal(ps.refresh().ok, true);
  const funds = ps.funds;
  const r = runStacking(m, ps, { enabled: { refresh: true } });
  assert.equal(r.accepted, 2); assert.equal(r.spent, 2); assert.equal(ps.funds, funds - 2);
  assert.equal(ps.round.refreshes, 3); assert.equal(r.modules.refresh.stopReason, 'refresh-cap');
  assert.equal(r.projectedLayerGain, 16);
  assert.equal(runStacking(m, ps, { enabled: { refresh: true } }).attempted, 0);
  m.dispatch(ps, 'onPrepEnd', {});
  assert.equal(ps.layers.siracusaShip, 22); assert.equal(ps.layers.arcaneShip, 6);
  check(h);
});

test('free refreshes stop at three; off is zero actions and no state/RNG mutation', t => {
  const { h, m, ps } = fixture(t);
  ps.shop.freeRefreshes = 9;
  const state = JSON.stringify([ps.privateView(), ps.round, m.rngShop]);
  assert.equal(runStacking(m, ps).stopReason, 'disabled');
  assert.equal(runStacking(m, ps, { enabled: false }).attempted, 0);
  assert.equal(JSON.stringify([ps.privateView(), ps.round, m.rngShop]), state);
  const r = runStacking(m, ps, { enabled: { refresh: true } });
  assert.equal(r.accepted, 3); assert.equal(r.netCost, 0); assert.equal(ps.shop.freeRefreshes, 6);
  check(h);
});

test('missing history stops; explicit action-derived fallback is honored', t => {
  const { h, m, ps } = fixture(t);
  assert.equal(ps.refresh().ok, true);
  const actual = ps.round;
  // A view adapter omits this history field, while real actions still use the real PlayerState.
  const view = new Proxy(ps, { get(target, key) {
    if (key === 'roundStats') return { gainedChess: actual.gainedChess };
    const v = Reflect.get(target, key); return typeof v === 'function' ? v.bind(target) : v;
  } });
  assert.equal(runStacking(m, view, { enabled: { refresh: true } }).modules.refresh.stopReason, 'unknown-refresh-history');
  const r = runStacking(m, view, { enabled: { refresh: true }, observedRefreshes: 1 });
  assert.equal(r.accepted, 2); assert.equal(actual.refreshes, 3);
  check(h);
});

test('acquire: two real paid buy/sell cycles; keeps board and existing unique key', t => {
  const { h, m, ps } = fixture(t, acquireTeam);
  const core = give(m, ps, id('拉普兰德'));
  const before = [...ps.board.values()].map(p => p.uid);
  offer(ps, cheap, 0); offer(ps, cheap, 1);
  const r = runStacking(m, ps, { enabled: { acquire: true } });
  assert.deepEqual(r.actions.map(a => a.action), ['buy', 'sell', 'buy', 'sell']);
  assert.equal(r.spent, 4); assert.equal(r.credited, 2); assert.equal(r.netCost, 2);
  assert.equal(ps.funds, 18); assert.equal(ps.round.gainedChess, 2);
  assert.deepEqual([...ps.board.values()].map(p => p.uid), before);
  assert.ok(ps.find(core.uid));
  m.dispatch(ps, 'onPrepEnd', {});
  assert.equal(ps.layers.yanShip, 6); assert.equal(ps.layers.arcaneShip, 4);
  check(h);
});

test('buying into a pair makes a new elite UID; never sells it or the existing core', t => {
  const { h, m, ps } = fixture(t, acquireTeam);
  const first = give(m, ps, cheap), second = give(m, ps, cheap);
  offer(ps, cheap);
  const r = runStacking(m, ps, { enabled: { acquire: true } });
  assert.equal(r.accepted, 1); assert.equal(r.actions[0].action, 'buy');
  assert.equal(r.modules.acquire.stopReason, 'identity-changed-retained');
  assert.equal(ps.find(first.uid), null); assert.equal(ps.find(second.uid), null);
  assert.ok(ps.allChess().some(p => p.id === m.gd.goldenIdOf(cheap)));
  check(h);
});

test('new paired copy and explicitly protected new unit are retained', t => {
  const { h, m, ps } = fixture(t, acquireTeam);
  give(m, ps, cheap); offer(ps, cheap);
  let r = runStacking(m, ps, { enabled: { acquire: true } });
  assert.equal(r.accepted, 1); assert.equal(ps.countCopies(cheap), 2);
  offer(ps, id('古米'));
  r = runStacking(m, ps, { enabled: { acquire: true }, coreIds: [id('古米')] });
  assert.equal(r.accepted, 1); assert.ok(ps.allChess().some(p => p.id === id('古米')));
  check(h);
});

test('Lappland: old copy already fired; only new spare adds four, is sold, real cost two', t => {
  const { h, m, ps } = fixture(t);
  const core = ps.allChess().find(p => p.id === id('拉普兰德'));
  assert.equal(ps.refresh().ok, true);
  const layers = ps.layers.siracusaShip;
  offer(ps, id('拉普兰德'));
  const r = runStacking(m, ps, { enabled: { lappland: true }, maxPurchases: 1 });
  assert.deepEqual(r.actions.map(a => a.action), ['buy', 'refresh', 'sell']);
  assert.equal(r.spent, 3); assert.equal(r.netCost, 2);
  assert.equal(ps.layers.siracusaShip - layers, 4);
  assert.ok(ps.find(core.uid)); assert.equal(ps.countCopies(id('拉普兰德')), 1);
  const n = ps.layers.siracusaShip;
  assert.equal(ps.refresh().ok, true); assert.equal(ps.layers.siracusaShip, n);
  check(h);
});

test('Lappland refuses a core-pair merge; no UID gamble', t => {
  const { h, m, ps } = fixture(t);
  give(m, ps, id('拉普兰德')); offer(ps, id('拉普兰德'));
  const r = runStacking(m, ps, { enabled: { lappland: true } });
  assert.equal(r.attempted, 0); assert.equal(r.modules.lappland.stopReason, 'merge-risk');
  assert.equal(ps.countCopies(id('拉普兰德')), 2); check(h);
});

test('interleaved real refresh after Lappland buy prevents a second witness refresh', t => {
  const { h, m, ps } = fixture(t);
  offer(ps, id('拉普兰德'));
  const steps = runStackingSteps(m, ps, { enabled: { lappland: true } });
  assert.equal(steps.next().value.action, 'buy');
  assert.equal(ps.refresh().ok, true);
  const end = steps.next();
  assert.equal(end.done, true); assert.equal(end.value.attempted, 1);
  assert.equal(end.value.modules.lappland.stopReason, 'refresh-history-changed-retained');
  assert.equal(ps.round.refreshes, 1); assert.equal(ps.countCopies(id('拉普兰德')), 2);
  check(h);
});

test('macro reads only current own state and public Match phase/round/gd, no hidden RNG or players', t => {
  const { h, m, ps } = fixture(t);
  const visibleMatch = new Proxy(m, { get(target, key) {
    assert.ok(['phase', 'round', 'gd'].includes(key), `hidden Match read: ${String(key)}`);
    return target[key];
  } });
  assert.equal(runStacking(visibleMatch, ps, { enabled: { refresh: true } }).accepted, 3);
  check(h);
});

test('reserve, cost-per-layer, net cost and action budgets bound spending', t => {
  const { h, m, ps } = fixture(t);
  ps.funds = 6;
  assert.equal(runStacking(m, ps, { enabled: true }).attempted, 0);
  ps.funds = 20;
  assert.equal(runStacking(m, ps, { enabled: true, maxCostPerLayer: 0 }).attempted, 0);
  const r = runStacking(m, ps, { enabled: { refresh: true }, maxNetCost: 1 });
  assert.equal(r.accepted, 1); assert.equal(r.netCost, 1);
  assert.equal(runStacking(m, ps, { enabled: true, maxActions: 1 }).accepted, 1);
  assert.equal(runStacking(m, ps, { enabled: true, maxActions: 0 }).attempted, 0);
  check(h);
});

test('full hand prevents buys; absent engine or capped layers prevents useless actions', t => {
  const { h, m, ps } = fixture(t, acquireTeam);
  for (const c of Object.values(DATA.chess).filter(c => c.visible && !c.isGolden)) {
    if (!ps.hand.includes(null)) break;
    if (!ps.allChess().some(p => m.gd.baseIdOf(p.id) === c.chessId)) give(m, ps, c.chessId);
  }
  offer(ps, cheap);
  assert.equal(runStacking(m, ps, { enabled: { acquire: true } }).attempted, 0);
  check(h);
  const other = fixture(t, []);
  assert.equal(runStacking(other.m, other.ps, { enabled: true }).attempted, 0);
  const capped = fixture(t);
  capped.ps.layers.siracusaShip = 999; capped.ps.layers.arcaneShip = 999; capped.ps.recompute();
  assert.equal(runStacking(capped.m, capped.ps, { enabled: { refresh: true } }).attempted, 0);
  check(capped.h);
});

test('real buy handler rejects a sold offer behind stale observation; stops immediately', t => {
  const { h, m, ps } = fixture(t, acquireTeam);
  offer(ps, cheap);
  const staleShop = { ...ps.shop, slots: ps.shop.slots.map(s => s ? { ...s } : null) };
  ps.shop.slots[0].sold = true;
  const view = new Proxy(ps, { get(target, key) {
    if (key === 'shop') return staleShop;
    const v = Reflect.get(target, key); return typeof v === 'function' ? v.bind(target) : v;
  } });
  const r = runStacking(m, view, { enabled: { acquire: true } });
  assert.equal(r.attempted, 1); assert.equal(r.accepted, 0);
  assert.match(r.stopReason, /^action-failed:/); assert.equal(ps.funds, 20);
  check(h);
});

test('step generator stops when legal sale fails after a successful purchase', t => {
  const { h, m, ps } = fixture(t, acquireTeam);
  offer(ps, cheap);
  const view = new Proxy(ps, { get(target, key) {
    if (key === 'ready') return false; // stale observer, actions bind to the real state
    const v = Reflect.get(target, key); return typeof v === 'function' ? v.bind(target) : v;
  } });
  const steps = runStackingSteps(m, view, { enabled: { acquire: true } });
  const buy = steps.next(); assert.equal(buy.value.action, 'buy');
  ps.ready = true;
  const sell = steps.next(); assert.equal(sell.value.action, 'sell'); assert.equal(sell.value.accepted, false);
  const end = steps.next(); assert.equal(end.done, true); assert.equal(end.value.attempted, 2);
  assert.equal(ps.funds, 18); assert.ok(ps.allChess().some(p => p.id === cheap));
  ps.ready = false; check(h);
});

test('loader hashes exact original, patches only explicit anchors, rejects mismatch; off returns identical object', async () => {
  const url = pathToFileURL(resolve(root, 'server/match/bot.js')).href;
  const source = readFileSync(new URL(url), 'utf8');
  assert.equal(createHash('sha256').update(source).digest('hex'), BOT_SHA256);
  assert.ok(source.includes(BEGIN_ANCHOR));
  const patched = transformStackingBot(source);
  assert.ok(patched.includes("yield* __arkStackingPrepSteps(m, ps, 'begin');"));
  assert.ok(patched.includes("yield* __arkStackingPrepSteps(m, ps, 'tail');"));
  assert.throws(() => transformStackingBot(source + '\n'), /SHA256 mismatch/);
  const original = process.env.ARK_STACKING;
  try {
    delete process.env.ARK_STACKING;
    const result = { format: 'module', source };
    assert.equal(await load(url, {}, async () => result), result);
  } finally { if (original == null) delete process.env.ARK_STACKING; else process.env.ARK_STACKING = original; }
  assert.deepEqual(environmentOptions({ ARK_STACKING: 'acquire' }).enabled, { refresh: false, acquire: true, lappland: false });
  assert.throws(() => environmentOptions({ ARK_STACKING: 'typo' }), /Unknown/);
});

test('begin and optional tail share action/purchase/cost budget in module-owned state', t => {
  const { h, m, ps } = fixture(t, acquireTeam);
  const saved = { ...process.env };
  t.after(() => { for (const k of ['ARK_STACKING', 'ARK_STACKING_TAIL', 'ARK_STACKING_OPTIONS']) {
    if (saved[k] == null) delete process.env[k]; else process.env[k] = saved[k];
  } });
  process.env.ARK_STACKING = 'acquire'; process.env.ARK_STACKING_TAIL = '1';
  process.env.ARK_STACKING_OPTIONS = JSON.stringify({ maxActions: 4, maxPurchases: 2 });
  offer(ps, cheap);
  for (const _ of stackingPrepSteps(m, ps, 'begin')) { /* drain */ }
  offer(ps, cheap);
  for (const _ of stackingPrepSteps(m, ps, 'tail')) { /* drain */ }
  const reports = stackingReports(ps);
  assert.equal(reports.length, 2); assert.equal(reports.reduce((n, r) => n + r.attempted, 0), 4);
  assert.equal(ps.funds, 18);
  for (const _ of stackingPrepSteps(m, ps, 'tail')) { /* no repeat */ }
  assert.equal(stackingReports(ps).length, 2); check(h);
});
