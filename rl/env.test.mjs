import assert from 'node:assert/strict';
import test from 'node:test';
import { ShopEnv } from './env.mjs';
import { encode } from './features.mjs';

// Every probe is a fresh real match, not a snapshot masquerading as a simulator clone.
test('initial legal actions execute through original methods; invalid index does not mutate the game', async () => {
  const e = await ShopEnv.create();
  try {
    const initial = e.reset({ seed: 7 });
    for (let index = 0; index < initial.candidates.length; index++) {
      const before = e.reset({ seed: 7 });
      assert.deepEqual(before.features, initial.features);
      const after = e.step(index);
      assert.equal(after.actionErrors, 0);
      assert.equal(after.engineErrors, 0);
    }
    const before = e.reset({ seed: 7 });
    assert.throws(() => e.step(-1), /Invalid candidateIndex/);
    assert.deepEqual(e.view().observation, before.observation);
    assert.equal(e.actionErrors, 1);
    const extended = { ...before.observation, seed: 999, victory: true, futureRng: [123] };
    assert.deepEqual(encode(extended, before.candidates, before.schema), before.features);
    assert.throws(() => e.reset({ mode: 'coop' }), /solo only/);
  } finally { e.close(); }
});

test('real multi-step full terminal, reproducible replay and explicit safety budget', async () => {
  const e = await ShopEnv.create();
  try {
    const run = () => {
      let s = e.reset({ seed: 11, budget: 4 }), actions = [], types = new Set();
      while (!s.done) {
        assert(s.features.mask.every(Boolean));
        assert.equal(s.candidates.length, s.features.candidates.length);
        let i;
        // Probe natural shop/merge/hand/round states with a deterministic policy.
        if (actions.length % 7 === 6) i = s.candidates.findIndex(a => a.type === 'ready');
        else if (!types.has('sell')) i = s.candidates.findIndex(a => a.type === 'sell');
        else if (!types.has('levelUp')) i = s.candidates.findIndex(a => a.type === 'levelUp');
        if (!(i >= 0)) i = s.candidates.findIndex(a => a.type === 'buy');
        if (!(i >= 0)) i = s.candidates.findIndex(a => a.type === 'refresh');
        if (!(i >= 0)) i = 0;
        actions.push(s.candidates[i]); types.add(s.candidates[i].type);
        s = e.step(i);
        assert.equal(s.actionErrors, 0);
        assert.equal(s.engineErrors, 0);
        assert(actions.length < 2000);
      }
      assert.equal(s.victory, !!s.summary.victory);
      assert.equal(s.reward, s.summary.victory ? 1 : -1);
      assert.equal(s.rounds, s.summary.roundsPassed);
      assert(s.budgetForcedReady > 0);
      const illicit = s.observation.history.actions.filter(a => ['buy', 'sell', 'refresh', 'levelUp'].includes(a.type) && a.source !== 'policy');
      assert.deepEqual(illicit, []);
      assert(types.has('buy') && types.has('sell') && types.has('refresh'));
      return { actions, summary: s.summary, rounds: s.rounds, steps: s.steps, engineErrors: s.engineErrors,
        actionErrors: s.actionErrors, budgetForcedReady: s.budgetForcedReady, victory: s.victory };
    };
    const first = run(), second = run();
    assert.deepEqual(first, second);
    console.log(JSON.stringify({ test: 'real-terminal', ...first, actions: first.actions.length, summary: undefined }));
  } finally { e.close(); }
});
