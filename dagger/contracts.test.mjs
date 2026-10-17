import assert from 'node:assert/strict';
import test from 'node:test';
import { ShopEnv } from '../rl/env.mjs';
import { annotate } from './env.mjs';
import { choose, query, staticRecords } from './teacher.mjs';

function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
const rngStates = env => Object.fromEntries(Object.entries(env.m)
  .filter(([k, v]) => k.startsWith('rng') && typeof v?.state === 'function')
  .map(([k, v]) => [k, v.state()]));

test('pure labels preserve real features, candidates, game and every match RNG; chooser executes exact label', async () => {
  const env = await ShopEnv.create();
  try {
    const records = freeze(staticRecords(env.data));
    let state = env.reset({ seed: 101, budget: 128 });
    for (let step = 0; step < 4 && !state.done; step++) {
      freeze(state);
      const before = JSON.stringify(state), rng = rngStates(env);
      const random = Math.random;
      Math.random = () => { throw new Error('Teacher consumed RNG'); };
      let label;
      try {
        label = annotate(state, records);
        assert.deepEqual(annotate(state, records), label);
        assert.equal(choose(state.observation, state.candidates, state.features.mask, records), label.teacherIndex);
      } finally { Math.random = random; }
      assert.deepEqual(rngStates(env), rng);
      assert.deepEqual(env.view(), state);
      assert.equal(JSON.stringify(state), before);
      const { teacherIndex, teacherVersion, teacherScores, teacherHash, ...original } = label;
      assert.deepEqual(original, state);
      assert(state.features.mask[teacherIndex]);
      const expected = state.candidates[teacherIndex];
      const oldActions = state.observation.history.actions.length;
      state = env.step(teacherIndex);
      const executed = state.observation.history.actions.slice(oldActions).find(a => a.source === 'policy');
      assert.equal(executed.type, expected.type);
      for (const key of ['slot', 'uid', 'id']) if (key in expected) assert.equal(executed[key], expected[key]);
      assert.equal(state.actionErrors + state.engineErrors + state.controllerErrors, 0);
    }
    // Terminal annotation has no actionable label; no simulated clone is used anywhere.
    const terminal = { ...state, done: true, candidates: [], features: { ...state.features, candidates: [], mask: [] } };
    assert.equal(annotate(terminal, records).teacherIndex, null);
  } finally { env.close(); }
});

test('scoring prioritizes merge, protects board/core, honors masks and observed refresh budget', () => {
  const records = { chess: { a: { baseId: 'a', upgradeNum: 3, upgradeChessId: 'b' } }, bonds: {} };
  const observation = { own: { board: [{ uid: 1, id: 'a', kind: 'chess' }],
    hand: [{ uid: 2, id: 'a', kind: 'chess' }, null], temp: [], bonds: [], deployCap: 1,
    deployCount: 1, funds: 10, shop: { level: 2, maxLevel: 6, upgradePrice: 8, freeRefreshes: 0 } },
    public: { round: 1 }, history: { rounds: {} } };
  const candidates = [{ type: 'ready' }, { type: 'buy', id: 'a', price: 2, tier: 1 },
    { type: 'sell', uid: 1, id: 'a', price: 1 }, { type: 'refresh', price: 1 }];
  assert.equal(query(observation, candidates, [true, true, true, true], records).teacherIndex, 1);
  assert.equal(query(observation, candidates, [true, false, true, true], records).teacherIndex, 3);
  observation.history.rounds['1'] = { refresh: 2 };
  assert.equal(query(observation, candidates, [true, false, true, true], records).teacherIndex, 0);
  observation.own.board[0].id = 'chess_char_2_16_a';
  candidates[2].id = observation.own.board[0].id;
  assert.equal(query(observation, candidates, [true, false, true, true], records).teacherScores[2].score, null);
});
