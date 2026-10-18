import assert from 'node:assert/strict';
import test from 'node:test';
import { CoopEnv } from './env.mjs';
import { ShopEnv } from '../rl/env.mjs';
import { encode } from '../rl/features.mjs';
import { MANIFEST, validateManifest, poolConfig, selectTeam } from './pool.mjs';
import { capacityFilteredBot } from './controller.mjs';
import { loadMovePreflight } from './preflight.mjs';

test('capacity filter rejects planner overflow before engine dispatch and restores methods', () => {
  let blocked = 0, dispatched = 0;
  const ps = {
    privateView: () => ({ hand: [{ uid: 1, kind: 'chess' }], temp: [],
      board: [{ uid: 2, kind: 'chess', row: 9, col: 2 }], deployCount: 1, deployCap: 1 }),
    move() { assert.equal(this, ps); dispatched++; return { ok: true }; },
  };
  const originalMove = ps.move;
  const bot = capacityFilteredBot({ arrange(m, p) {
    assert.equal(p.move(1, { area: 'board', row: 9, col: 3 }).error, 'COOP_CONTROLLER_CAPACITY_FILTER');
    assert.equal(dispatched, 0);
    assert(p.move(1, { area: 'board', row: 9, col: 2 }).ok);
    throw new Error('test planner failure');
  } }, () => blocked++);
  assert.throws(() => bot.arrange({}, ps), /test planner failure/);
  assert.equal(blocked, 1);
  assert.equal(dispatched, 1);
  assert.equal(ps.move, originalMove);
});

test('strict frozen identities, disjoint heldout and reproducible selection', () => {
  for (const env of [{ COOP_POOL: 'heldout' }, { COOP_POOL: 'typo' }, { COOP_SPLIT: 'heldout' },
    { COOP_TEAM: '' }, { COOP_TEAM: 'original-v1,buy2-v1,random-v1' }, { COOP_POOL_SEED: '4294967296' },
    { COOP_POOL_SEED: '1.0' }, { COOP_POOL_SEED: '0' }]) assert.throws(() => poolConfig(env));
  const bad = structuredClone(MANIFEST);
  bad.members[3].family = 'first-offer-buy2';
  assert.throws(() => validateManifest(bad), /identity/);
  const badPool = structuredClone(MANIFEST);
  badPool.pools.mixed.push('random-v1');
  assert.throws(() => validateManifest(badPool), /pool/);
  assert(Object.isFrozen(MANIFEST.members[0]));
  const cfg = poolConfig({ COOP_POOL_SEED: '123' });
  assert.deepEqual(selectTeam(cfg, 11), selectTeam(cfg, 11));
  const draws = new Set(Array.from({ length: 30 }, (_, i) => JSON.stringify(selectTeam(cfg, i + 1))));
  assert(draws.size > 1);
});

test('real coop terminals, pool behavior, seat ownership, per-round actions, replay, visible schema', async () => {
  const base = await ShopEnv.create();
  const deps = { Match: base.Match, VirtualScheduler: base.VirtualScheduler, data: base.data, bot: base.bot, movePreflight: await loadMovePreflight() };
  const run = (pool, fixed) => {
    const config = poolConfig({ COOP_POOL: pool, COOP_SPLIT: 'eval', ...(fixed ? { COOP_TEAM: fixed } : {}) });
    const e = new CoopEnv(deps, config);
    try {
      let s = e.reset({ seed: 11, mode: 'solo', difficulty: 'ABYSS', budget: 4 });
      assert.equal(e.m.mode, 'coop');
      assert.equal(e.m.players.size, 4);
      assert.equal(s.episode.mode, 'coop');
      assert.deepEqual(s.schema, base.schema);
      assert(!('partnerAudit' in s));
      assert.deepEqual(s.observation.own, e.ps.privateView());
      const mates = e.m.order.slice(1), privateViews = mates.map(p => p.privateView);
      mates.forEach(p => { p.privateView = () => { throw new Error('Other private read'); }; });
      assert.deepEqual(e.view().features, s.features);
      mates.forEach((p, i) => { p.privateView = privateViews[i]; });
      const rngState = () => Object.fromEntries(Object.keys(e.m).filter(k => k.startsWith('rng')).map(k => [k, e.m[k].state()]));
      const beforeRng = rngState();
      for (let i = 1; i <= 20; i++) selectTeam(config, i);
      assert.deepEqual(rngState(), beforeRng);
      assert.deepEqual(encode({ ...s.observation, future: 'sentinel', rng: beforeRng }, s.candidates, s.schema), s.features);
      const originalEnd = e.m.endPrep.bind(e.m);
      let checkedPreps = 0;
      e.m.endPrep = () => {
        for (const p of e.m.alivePlayers().filter(p => p !== e.ps)) {
          const row = e.partnerAudit[p.playerId].rounds[e.m.round];
          assert.equal(row.scheduled, 1);
          assert.equal(row.ready, 1);
          assert(p.ready);
        }
        checkedPreps++;
        originalEnd();
      };
      const actions = [];
      // First buy must alter the actual primary shop; no bot can substitute an action.
      let i = s.candidates.findIndex(a => a.type === 'buy');
      assert(i >= 0);
      const first = s.candidates[i];
      s = e.step(i); actions.push(first);
      assert(e.ps.shop.slots[first.slot].sold);
      assert(e.history.actions.some(a => a.source === 'policy' && a.type === 'buy' && a.id === first.id));
      while (!s.done) {
        const types = new Set(actions.map(a => a.type));
        const desired = !types.has('sell') ? 'sell' : !types.has('refresh') ? 'refresh' : !types.has('levelUp') ? 'levelUp' : 'buy';
        i = s.candidates.findIndex(a => a.type === desired);
        if (i < 0) i = s.candidates.findIndex(a => a.type === 'ready');
        if (i < 0) i = 0;
        actions.push(s.candidates[i]);
        s = e.step(i);
        assert(actions.length < 500);
        assert.equal(s.engineErrors, 0);
        assert.equal(s.actionErrors, 0);
        assert.equal(s.controllerErrors, e.controllerDiagnosticErrors);
        if (!s.done) { assert.equal(s.reward, 0); assert.equal(s.summary, null); }
      }
      assert(checkedPreps > 1);
      assert.equal(e.m.phase, 'RESULT');
      assert.equal(s.summary.players.length, 4);
      assert.equal(s.victory, s.summary.victory);
      assert.equal(s.reward, s.summary.victory ? 1 : -1);
      assert.equal(s.rounds, s.summary.roundsPassed);
      assert.equal(s.summary.errors, 0);
      assert.deepEqual(e.history.actions.filter(a => ['buy', 'sell', 'refresh', 'levelUp'].includes(a.type) && a.source !== 'policy'), []);
      assert(actions.some(a => a.type === 'buy') && actions.some(a => a.type === 'sell'));
      for (const a of Object.values(s.partnerAudit)) {
        assert.equal(a.rejected, a.rejections.length);
        assert(a.rejections.every(r => r.diagnostic));
      }
      const report = { pool, victory: s.victory, rounds: s.rounds, checkedPreps, steps: s.steps,
        engineErrors: s.engineErrors, actionErrors: s.actionErrors, controllerErrors: s.controllerErrors,
        partnerAudit: s.partnerAudit };
      console.log(JSON.stringify({ ...report, partnerAudit: Object.fromEntries(Object.entries(s.partnerAudit).map(([id, a]) =>
        [id, { actions: a.actions, preps: Object.keys(a.rounds).length, rejected: a.rejected, actionSHA256: a.actionSHA256 }])) }));
      return { report, actions, summary: s.summary };
    } finally { e.close(); }
  };
  const original = run('normal');
  const fixed = 'buy2-v1,saver-v1,buy2-v1';
  const weak = run('weak', fixed);
  const heldout = run('heldout');
  assert.notDeepEqual(original.report.partnerAudit, weak.report.partnerAudit);
  assert.notDeepEqual(heldout.report.partnerAudit, weak.report.partnerAudit);
  assert.deepEqual(weak, run('weak', fixed));
  assert.equal(weak.report.partnerAudit['partner-1'].rounds[1].actions.buy, 2);
  assert(Object.values(weak.report.partnerAudit['partner-2'].rounds).some(r => r.actions.levelUp === 1));
  const e = new CoopEnv(deps, poolConfig());
  try {
    for (const seed of [0, -1, 2 ** 32, 1.5]) assert.throws(() => e.reset({ seed }));
    e.reset({ seed: 7 });
    const before = e.observe();
    assert.throws(() => e.step(-1), /Invalid candidateIndex/);
    assert.deepEqual(e.observe(), before);
    assert.equal(e.actionErrors, 1);
  } finally { e.close(); base.close(); }
});
