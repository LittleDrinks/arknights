// Loaded through the REAL loader. Only fake player/scheduler/bot dependencies;
// no create/reset, engine imports, Match, Battle, training or checkpoint IO.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { load, transform, TARGET, ORIGINAL_VERSION, VERSION, REPLACEMENTS } from './loader.mjs';
import { ShopEnv } from '../rl/env.mjs';
import { CoopEnv } from '../coop/env.mjs';

const timing = process.env.ARK_PREP_TIMING;
const deferred = timing === 'deferred';
const expectedVersion = `coop/move-preflight-v3+${deferred ? VERSION : ORIGINAL_VERSION}`;
const source = fs.readFileSync(new URL('../rl/env.mjs', import.meta.url), 'utf8');
const transformed = transform(source);
assert.equal(REPLACEMENTS.length, 3);
let reconstructed = source;
for (const [from, to] of REPLACEMENTS) {
  assert.equal(reconstructed.split(from).length, 2);
  reconstructed = reconstructed.replace(from, to);
}
assert.equal(transformed, reconstructed);
for (const method of ['candidates(', 'installRecorder()', 'observe()']) {
  // Complete source is pinned; additionally check the unchanged feature/action region.
  assert.ok(source.includes(method));
}
assert.equal(source.slice(source.indexOf('  candidates('), source.indexOf('  advance()')),
  transformed.slice(transformed.indexOf('  candidates('), transformed.indexOf('  advance()')));
const forced = text => text.slice(text.indexOf('    if (!this.ps.ready && this.history.prepSteps'),
  text.indexOf('    this.advance();', text.indexOf('  step(')));
assert.equal(forced(source), forced(transformed));
assert.throws(() => transform(source + ' '), /SHA256/);
assert.throws(() => transform(source.replace('control(this);', 'control(this); control(this);')), /SHA256/);
for (const mode of ['off', 'deferred']) {
  process.env.ARK_PREP_TIMING = mode;
  for (const bytes of [source, Buffer.from(source)]) {
    const result = { format: 'module', source: bytes };
    const loaded = await load(TARGET, {}, async () => result);
    if (mode === 'off') { assert.equal(loaded, result); assert.equal(loaded.source, bytes); }
    else assert.equal(loaded.source, transformed);
    for (const url of [TARGET + '?copy=1', TARGET + '#copy', 'file:///unrelated/rl/env.mjs']) {
      assert.equal(await load(url, {}, async () => result), result);
    }
  }
  await assert.rejects(load(TARGET, {}, async () => ({ source: source + ' ' })), /SHA256/);
}
process.env.ARK_PREP_TIMING = 'typo';
await assert.rejects(load(TARGET, {}, async () => ({ source })), /require off or deferred/);
process.env.ARK_PREP_TIMING = timing;

function fixture({ temp = [], budget = 128, block = null, arrangeEffect, choiceEffect } = {}) {
  const events = [];
  let env;
  const ps = {
    alive: true, ready: false, playerId: 'learner', seat: 0, readyAllowed: true,
    hand: [null, null], temp, board: new Map(),
    gd: { item: () => ({ itemType: 'EQUIP', kind: 'equipment' }), chess: () => ({ tier: 1 }),
      sellPrice: () => 1, equipPerChess: 2 },
    privateView() {
      return { playerId: this.playerId, seat: 0, alive: this.alive, ready: this.ready,
        canReady: this.readyAllowed && !this.temp.some(Boolean), lp: 100, funds: 10,
        board: [...this.board.values()], hand: this.hand, temp: this.temp, bonds: [], effects: {},
        deployCount: this.board.size, deployCap: 1,
        shop: { level: 1, maxLevel: 6, upgradePrice: 2, refreshPrice: 1, freeRefreshes: 0,
          slots: [{ id: 'unit', kind: 'chess', price: 1, sold: false }], rewardOffer: null } };
    },
    find(uid) { return [...this.hand, ...this.temp, ...this.board.values()].find(p => p?.uid === uid); },
    buy() { events.push('buy'); this.hand[0] = { uid: 'u1', id: 'unit', kind: 'chess' }; return { ok: true }; },
    refresh() { events.push('refresh'); return { ok: true }; },
    sell() { throw new Error('Must not sell for policy'); },
    move(uid, to) {
      events.push('move');
      const p = this.find(uid);
      this.hand = this.hand.map(x => x?.uid === uid ? null : x);
      this.temp = this.temp.filter(x => x?.uid !== uid);
      this.board.set('0,0', { ...p, row: to.row, col: to.col });
      return { ok: true };
    },
    equip() { events.push('equip'); return { ok: true }; },
    destroy(uid) { events.push('destroy'); this.temp = this.temp.filter(p => p?.uid !== uid); return { ok: true }; },
    setReady(value) {
      assert.equal(value, true);
      assert.equal(this.privateView().canReady, true, 'never illegal ready');
      events.push('ready'); this.ready = true; return { ok: true };
    },
    resolveTemp() { events.push('resolveTemp'); this.temp = []; },
  };
  const deps = { data: {}, movePreflight: () => block, bot: { arrange(m, player) {
    assert.equal(env.source, 'fixed-controller');
    events.push('arrange');
    for (const piece of [...player.hand, ...player.temp].filter(p => p?.kind === 'chess')) {
      player.move(piece.uid, { area: 'board', row: 0, col: 0 });
    }
    arrangeEffect?.(player);
  } } };
  env = new CoopEnv(deps, {}); // Actual constructor wraps arrange in capacity/preflight.
  Object.assign(env, { ps, summary: null, steps: 0, source: 'policy', prepRound: 0, lastBondState: null,
    actionErrors: 0, controllerErrors: 0, controllerDiagnosticErrors: 0, fatalRejections: 0,
    controllerBlockedSales: 0, controllerBlockedMoves: 0, controllerBlockedMoveReasons: [],
    budgetForcedReady: 0, engineLogErrors: [], partnerAudit: {},
    history: { actions: [], bonds: [], pieces: {}, totals: {}, rounds: {}, byId: {}, prepSteps: 0, prepBudget: budget },
    sched: { errors: [], runUntil() { return true; } },
    m: { phase: 'PREP', round: 1, errorCount: 0,
      publicView() { return { phase: this.phase, round: this.round, difficulty: 'ABYSS' }; },
      autoPickPersonalChoice(player, who) {
        assert.equal(player, ps); assert.equal(who, 'bot'); assert.equal(env.source, 'fixed-controller');
        events.push('choice'); choiceEffect?.(player);
      } },
  });
  assert.equal(env.view().controller, expectedVersion); // Actual inherited view, not metadata-only ID.
  assert.equal(Object.getPrototypeOf(CoopEnv.prototype), ShopEnv.prototype);
  env.view = () => ({ steps: env.steps }); // Avoid synthesizing a game terminal after fake ready.
  const step = type => {
    const cs = env.candidates();
    const index = cs.findIndex(a => a.type === type);
    assert.ok(index >= 0, `candidate ${type}`);
    return env.step(index);
  };
  return { env, ps, events, step };
}

// First advance and ordinary economy: no arrange/equipment/reward until ready in deferred.
{
  const { env, ps, events, step } = fixture();
  env.advance();
  assert.deepEqual(events, deferred ? ['choice'] : ['choice', 'arrange']);
  events.length = 0;
  step('buy');
  assert.deepEqual(events, deferred ? ['buy', 'choice'] : ['buy', 'choice', 'arrange', 'move']);
  assert.equal(ps.board.size, deferred ? 0 : 1);
  events.length = 0;
  step('ready');
  assert.deepEqual(events, deferred ? ['choice', 'arrange', 'move', 'ready'] : ['ready']);
}

// CURRENT temp after auto-pick, not stale temp captured before it, triggers full original v1.
{
  let first = true;
  const { env, ps, events } = fixture({ choiceEffect(player) {
    if (first) { first = false; player.temp.push({ uid: 't1', id: 'unit', kind: 'chess' }); }
  } });
  env.advance();
  assert.deepEqual(events, deferred ? ['choice', 'choice', 'arrange', 'move'] : ['choice', 'arrange', 'move']);
  assert.equal(ps.temp.length, 0);
}
{
  const { env, events } = fixture({ temp: [null] });
  env.advance();
  assert.deepEqual(events, deferred ? ['choice'] : ['choice', 'arrange']);
}

// Item work is skipped with empty temp, but retains original v1 during fallback.
{
  const { env, ps, events } = fixture();
  ps.hand[0] = { uid: 'equip', id: 'eq', kind: 'item' };
  ps.board.set('0,0', { uid: 'carrier', id: 'unit', kind: 'chess', items: [] });
  env.advance();
  assert.equal(events.includes('equip'), !deferred);
}
{
  const { env, events } = fixture({ temp: [{ uid: 'art', id: 'art', kind: 'item' }] });
  env.advance();
  assert.ok(events.includes('arrange') && events.includes('destroy'));
}

// Ready is chosen before control. Control may invalidate it: failed without setReady.
if (deferred) {
  const { env, ps, events, step } = fixture({ arrangeEffect(player) { player.readyAllowed = false; } });
  assert.ok(env.candidates().some(a => a.type === 'ready'));
  assert.throws(() => step('ready'), /PREP_TIMING_READY_INVALID/);
  assert.equal(events.includes('ready'), false);
  assert.equal(env.actionErrors, 1);
  assert.equal(env.steps, 0);
  assert.equal(ps.ready, false);
  assert.equal(ps.privateView().canReady, false);
  assert.equal(env.candidates().some(a => a.type === 'ready'), false);
}

// Candidates remain native: temp blocks ready; no synthetic deploy/discard action is added.
{
  const { env } = fixture({ temp: [{ uid: 't', id: 'unit', kind: 'chess' }] });
  assert.equal(env.candidates().some(a => a.type === 'ready'), false);
  assert.ok(env.candidates().every(a => ['buy', 'sell', 'refresh', 'levelUp', 'ready'].includes(a.type)));
}

// Forced budget still calls full v1, resolves temp and submits ready in original order.
{
  const { env, events, step } = fixture({ budget: 1 });
  step('refresh');
  assert.deepEqual(events, ['refresh', 'choice', 'arrange', 'resolveTemp', 'ready']);
  assert.equal(env.budgetForcedReady, 1);
  assert.equal(env.history.actions[0].type, 'resolveTemp');
}

// Retained native CoopEnv wrappers filter before movement (both timing paths).
for (const capacity of [false, true]) {
  const { env, ps, events, step } = fixture({ block: capacity ? null : 'synthetic-preflight' });
  ps.hand[0] = { uid: 'u1', id: 'unit', kind: 'chess' };
  if (capacity) ps.board.set('other', { uid: 'u2', id: 'unit', kind: 'chess', row: 1, col: 0 });
  if (deferred) step('ready'); else env.advance();
  assert.equal(env.controllerBlockedMoves, 1);
  assert.equal(events.includes('move'), false);
  assert.equal(env.arranging.has(ps), false);
}

console.log(`synthetic timing/loader passed: ${expectedVersion}`);
