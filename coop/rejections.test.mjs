import assert from 'node:assert/strict';
import test from 'node:test';
import { CoopEnv } from './env.mjs';
import { ShopEnv } from '../rl/env.mjs';
import { poolConfig } from './pool.mjs';
import { rejectionState, checkedProposal } from './rejections.mjs';
import { control } from '../rl/controller.mjs';
import { capacityFilteredBot } from './controller.mjs';
import { loadMovePreflight } from './preflight.mjs';

async function createBase() {
  const base = await ShopEnv.create();
  base.movePreflight = await loadMovePreflight();
  return base;
}

const fixedWeak = () => poolConfig({ COOP_POOL: 'weak', COOP_TEAM: 'buy2-v1,saver-v1,buy2-v1' });

test('original validation rejection remains audited and the real game continues', async () => {
  const base = await createBase();
  const e = new CoopEnv(base, poolConfig({ COOP_POOL: 'normal' }));
  try {
    const s = e.reset({ seed: 11, difficulty: 'ABYSS', budget: 2 });
    const p = e.m.order[1], before = structuredClone(p.privateView());
    const result = p.buy(-1);
    assert.equal(result.error, 'BAD_TARGET');
    assert.deepEqual(p.privateView(), before);
    assert.equal(e.partnerAudit[p.playerId].rejected, 1);
    const next = e.step(s.candidates.findIndex(a => a.type === 'ready'));
    assert(next.rounds > s.rounds);
    assert.equal(next.actionErrors, 0);
    assert(!('partnerAudit' in next));
    assert.equal(e.partnerAudit[p.playerId].rejections[0].result.error, 'BAD_TARGET');
    assert.equal(e.partnerAudit[p.playerId].rejections[0].diagnostic, true);
  } finally { e.close(); }
});

test('arrange BAD_TILE is filtered before recorder and engine dispatch', async () => {
  const base = await createBase();
  let rejected;
  const e = new CoopEnv({ ...base, bot: { ...base.bot, arrange(m, ps) {
    const piece = ps.hand.find(p => p?.kind === 'chess');
    if (piece) rejected = ps.move(piece.uid, { area: 'board', row: -1, col: -1 }, 'RIGHT');
  } } }, fixedWeak());
  try {
    const s = e.reset({ seed: 11, difficulty: 'ABYSS', budget: 4 });
    const next = e.step(s.candidates.findIndex(a => a.type === 'buy' && !a.id.includes('item')));
    assert.equal(rejected.error, 'COOP_BAD_TILE_FILTER');
    assert.equal(next.controllerErrors, 0);
    assert.equal(next.actionErrors, 0);
    assert.equal(e.controllerDiagnosticErrors, 0);
    assert.equal(e.controllerBlockedMoves, 1);
    assert.equal(e.controllerBlockedMoveReasons[0].reason, 'destination-out-of-field');
    assert.equal(e.rejectedActions.length, 0);
    assert(!e.history.actions.some(a => a.type === 'move'));
    assert.deepEqual(next.schema, s.schema);
  } finally { e.close(); }
});

test('only known unchanged validation qualifies; effects and hidden mutations stay fatal', async () => {
  const base = await createBase();
  const e = new CoopEnv(base, poolConfig({ COOP_POOL: 'normal' }));
  try {
    e.reset({ seed: 11, difficulty: 'ABYSS' });
    const p = e.m.order[1];
    // Real engine rejection, not a fabricated success or a private-view-only comparison.
    const before = rejectionState(e.m);
    assert.equal(p.buy(-1).error, 'BAD_TARGET');
    assert(before.equals(rejectionState(e.m)));
    for (const method of ['buy', 'sell', 'move', 'equip', 'pickReward', 'useArt', 'destroy']) {
      const r = p[method](-1);
      assert.equal(r.error, 'BAD_TARGET');
      const record = e.partnerAudit[p.playerId].rejections.at(-1);
      assert.equal(record.diagnostic, true, JSON.stringify({ method, reason: record.reason, pathdiff: record.pathdiff }));
    }
    const reject = invoke => e.callPlayer(p, 'buy', [-1], invoke, true);
    const unknown = reject(() => ({ error: 'NEW_ERROR' }));
    assert.equal(unknown.reason, 'unknown-rejection', JSON.stringify(unknown));
    const mutations = [
      () => p.funds++, () => { p.counters.hiddenProbe = 1; },
      () => { e.ps.pendingFunds++; }, () => e.m.rngShop(),
      () => { e.m.pool.entries.values().next().value.left--; },
      () => { e.m.itemPool.debt.set('probe', 1); },
      () => e.m.nextUid(), () => e.sched.setTimeout(() => {}, 1),
    ];
    for (const mutate of mutations) {
      const record = reject(() => { mutate(); return { error: 'BAD_TARGET' }; });
      assert.equal(record.diagnostic, false);
      assert.equal(record.reason, 'state-changed');
      assert(record.pathdiff.length > 0);
      assert(record.pathdiff.every(p => /^(players\[\d+\]\.\w+|match\.\w+|rng\.\w+|scheduler\.\w+|pool|itemPool)$/.test(p)));
    }
    assert.throws(() => e.assertHealthy(), /Invalid coop rollout/);
    const dispatch = e.m.dispatchItem;
    e.m.dispatchItem = () => {}; // Model an effect rejection even with no visible mutation.
    const record = checkedProposal(p, 'useArt', () => {
      e.m.dispatchItem(); return { error: 'BAD_TARGET' };
    });
    assert.equal(record.diagnostic, false);
    assert.equal(record.reason, 'effect-dispatched');
    const stub = e.m.dispatchItem;
    assert.throws(() => checkedProposal(p, 'equip', () => { throw new Error('effect crash'); }), /effect crash/);
    assert.equal(e.m.dispatchItem, stub);
    e.m.dispatchItem = dispatch;
  } finally { e.close(); }
});

test('weak actions, primary candidates, unknown controller failures and exceptions abort', async () => {
  const base = await createBase();
  const e = new CoopEnv(base, fixedWeak());
  const normalBot = e.bot;
  try {
    e.reset({ seed: 11, difficulty: 'ABYSS' });
    const p = e.m.order[1];
    assert.equal(p.buy(-1).error, 'BAD_TARGET');
    assert.equal(e.actionErrors, 1);
    assert.equal(e.partnerAudit[p.playerId].rejected, 1);
    assert.equal(e.partnerAudit[p.playerId].rejections[0].diagnostic, false);
    assert.throws(() => e.view(), /Invalid coop rollout/);

    let s = e.reset({ seed: 11, difficulty: 'ABYSS' });
    // A stale legal candidate must still fail via the real player.buy boundary.
    const index = s.candidates.findIndex(a => a.type === 'buy');
    e.ps.shop.slots[s.candidates[index].slot].sold = true;
    const candidates = e.candidates;
    e.candidates = () => s.candidates;
    assert.throws(() => e.step(index), /Candidate rejected/);
    e.candidates = candidates;
    assert.equal(e.actionErrors, 1);
    assert.equal(e.rejectedActions.at(-1).diagnostic, false);

    e.reset({ seed: 11, difficulty: 'ABYSS' });
    e.bot = capacityFilteredBot({ arrange: (m, ps) => ps.move(-1, { area: 'board', row: 10, col: 5 }, 'RIGHT') }, () => {}, e.arranging, e.movePreflight);
    control(e);
    assert.equal(e.controllerErrors, 1);
    assert.equal(e.controllerDiagnosticErrors, 0);
    assert.throws(() => e.view(), /Invalid coop rollout/);

    e.bot = normalBot;
    e.reset({ seed: 11, difficulty: 'ABYSS' });
    assert.throws(() => e.callPlayer(e.ps, 'buy', [], () => { throw new Error('engine crash'); }, true), /engine crash/);
    assert.throws(() => e.view(), /Invalid coop rollout/); // Latched if upstream tryDo swallowed it.
    e.reset({ seed: 11, difficulty: 'ABYSS' });
    e.sched.errors.push(new Error('scheduler crash'));
    assert.throws(() => e.view(), /Invalid coop rollout/);
  } finally { e.close(); }
});

test('diagnostics stay private until a real terminal and rejected calls are never successful actions', async () => {
  const base = await createBase();
  const e = new CoopEnv(base, poolConfig({ COOP_POOL: 'mixed', COOP_TEAM: 'original-v1,buy2-v1,saver-v1' }));
  try {
    let s = e.reset({ seed: 11, difficulty: 'ABYSS', budget: 1 });
    const p = e.m.order[1];
    const audit = e.partnerAudit[p.playerId];
    const actions = audit.actions, hash = audit.hash.copy().digest('hex');
    assert.equal(p.buy(-1).error, 'BAD_TARGET');
    assert.equal(audit.actions, actions);
    assert.equal(audit.hash.copy().digest('hex'), hash);
    while (!s.done) {
      for (const key of ['partnerAudit', 'partnerActionErrors', 'rejectedActions', 'controllerDiagnosticErrors', 'controllerBlockedMoveReasons', 'filteredMoves']) assert(!(key in s));
      assert(!('diagnostics' in s.episode));
      s = e.step(s.candidates.findIndex(a => a.type === 'ready'));
    }
    assert.equal(e.m.phase, 'RESULT');
    assert.equal(s.summary.players.length, 4);
    assert.equal(s.reward, s.summary.victory ? 1 : -1);
    assert(s.partnerAudit[p.playerId].rejected >= 1);
    assert.equal(s.partnerAudit[p.playerId].rejected, s.partnerAudit[p.playerId].rejections.length);
    assert.equal(s.partnerAudit[p.playerId].rejections[0].result.error, 'BAD_TARGET');
    assert.equal(s.partnerActionErrors, Object.values(s.partnerAudit).reduce((n, a) => n + a.rejected, 0));
    assert.equal(s.actionErrors, 0);
  } finally { e.close(); }
});

test('terminal diagnostics travel in episode metadata without changing schema', async () => {
  const base = await createBase();
  const e = new CoopEnv(base, poolConfig({ COOP_POOL: 'heldout', COOP_SPLIT: 'eval' }));
  try {
    let s = e.reset({ seed: 11, difficulty: 'ABYSS', budget: 1 });
    while (!s.done) {
      assert(!('diagnostics' in s.episode));
      s = e.step(s.candidates.findIndex(a => a.type === 'ready'));
    }
    assert.equal(e.m.phase, 'RESULT');
    assert.deepEqual(s.schema, base.schema);
    assert.deepEqual(s.episode.diagnostics.partnerAudit, s.partnerAudit);
    assert.deepEqual(s.episode.diagnostics.rejectedActions, s.rejectedActions);
    assert.equal(s.episode.diagnostics.partnerActionErrors, s.partnerActionErrors);
    assert.equal(s.episode.diagnostics.controllerDiagnosticErrors, s.controllerDiagnosticErrors);
    assert(!('diagnostics' in e.episode)); // super.view copied metadata; no persistent leak.
    assert(!('diagnostics' in e.reset({ seed: 11, difficulty: 'ABYSS', budget: 1 }).episode));
  } finally { e.close(); }
});

test('a primary arrange BAD_TILE with hidden mutation cannot be exempted', async () => {
  const base = await createBase();
  class PollutedMatch extends base.Match {
    constructor(options) {
      super(options);
      this.players.get('learner').move = function () {
        this.counters.hiddenPollution = 1;
        return { error: 'BAD_TILE' };
      };
    }
  }
  const e = new CoopEnv({ ...base, Match: PollutedMatch,
    bot: { arrange: (m, ps) => ps.move(10, { area: 'board', row: 10, col: 5 }, 'RIGHT') } }, fixedWeak());
  try {
    assert.throws(() => e.reset({ seed: 11, difficulty: 'ABYSS' }), /Invalid coop rollout/);
    assert.equal(e.controllerErrors, 1);
    assert.equal(e.controllerDiagnosticErrors, 0);
    assert.equal(e.ps.counters.hiddenPollution, 1); // No rollback concealing the failure.
    assert.equal(e.rejectedActions[0].reason, 'state-changed');
    assert(e.rejectedActions[0].pathdiff.includes('players[0].counters'));
  } finally { e.close(); }
});
