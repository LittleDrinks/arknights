// Run through the production deferred loader, with fake dependencies only.
// No create/reset/step, real Match/Battle, or saved actions are executed.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { CoopEnv } from '../coop/env.mjs';
import { verifyState, verifyTerminal, validateTrace } from '../coop-replay/verify.mjs';
import { profileFor } from '../coop-replay/profiles.mjs';
import { PROFILE, CONTROLLER } from './profile.mjs';

const trace = JSON.parse(fs.readFileSync(new URL('../../experiments/wide-seed65-frozen-trace-v1.json', import.meta.url)));
const counters = ['partnerActionErrors', 'controllerDiagnosticErrors'];
const { diagnostics, ...episode } = structuredClone(trace.terminal.episode);
const env = new CoopEnv({ data: {}, bot: {}, movePreflight: () => null }, {});
Object.assign(env, {
  episode, summary: null, steps: 0, m: { round: 1, errorCount: 0 }, sched: { errors: [] },
  engineLogErrors: [], actionErrors: 0, controllerErrors: 0, fatalRejections: 0,
  partnerActionErrors: 0, controllerDiagnosticErrors: 0, controllerBlockedSales: 0,
  controllerBlockedMoves: 0, controllerBlockedMoveReasons: [], filteredMoves: [],
  rejectedActions: [], partnerBlockedSales: 0, budgetForcedReady: 0,
  partnerAudit: Object.fromEntries([1, 2, 3].map(i => [`partner-${i}`, {
    rounds: {}, actions: 0, rejected: 0, rejections: [], filtered: 0, filteredMoves: [],
    hash: createHash('sha256'),
  }])),
  observe: () => structuredClone(trace.terminal.observation),
  candidates: () => env.summary ? [] : [{ type: 'ready' }],
});
assert.equal(env.view, CoopEnv.prototype.view);

// Reset-shaped (step 0) and later nonterminal views use the REAL inherited view
// and health checks; only observation/candidate/engine dependencies are fake.
for (const steps of [0, 1]) {
  env.steps = steps;
  const state = env.view();
  assert.equal(state.done, false);
  assert.equal(state.controller, CONTROLLER);
  for (const key of counters) assert.equal(Object.hasOwn(state, key), false);
  verifyState(state, trace, PROFILE, env);
  assert.throws(() => verifyState(state, trace, PROFILE), /env\./);
  // Neither invented state fields nor missing live counters count as evidence.
  assert.throws(() => verifyState({ ...state, partnerActionErrors: 0, controllerDiagnosticErrors: 0 }, trace, PROFILE), /env\./);
  for (const key of counters) {
    for (const value of [1, undefined, null]) {
      env[key] = value;
      assert.throws(() => verifyState(state, trace, PROFILE, env), new RegExp(`env\\.${key}`));
    }
    delete env[key];
    assert.throws(() => verifyState(state, trace, PROFILE, env), new RegExp(`env\\.${key}`));
    env[key] = 0;
  }
  // The default training API still accepts nonterminal views without these fields.
  const training = { ...state, controller: profileFor().controller };
  verifyState(training, trace);
  verifyState(training, trace, undefined, { partnerActionErrors: 1, controllerDiagnosticErrors: 1 });
}

env.summary = structuredClone(trace.terminal.summary);
const terminal = env.view();
const syntheticTrace = { terminal: { ok: true, ...structuredClone(terminal) } };
verifyState(terminal, syntheticTrace, PROFILE);
verifyState(terminal, syntheticTrace, PROFILE, env);
verifyTerminal(terminal, syntheticTrace);
for (const key of counters) {
  env[key] = 1;
  assert.throws(() => verifyState(terminal, syntheticTrace, PROFILE, env), new RegExp(`env\\.${key}`));
  env[key] = 0;
  for (const value of [undefined, 1]) {
    const bad = structuredClone(terminal);
    if (value === undefined) delete bad[key];
    else bad[key] = value;
    assert.throws(() => verifyState(bad, syntheticTrace, PROFILE), new RegExp(key));
    assert.throws(() => verifyState(bad, syntheticTrace, PROFILE, env), new RegExp(key));
    assert.throws(() => verifyTerminal(bad, syntheticTrace), /Complete terminal mismatch/);
    const badTrace = structuredClone(trace);
    if (value === undefined) delete badTrace.terminal[key];
    else badTrace.terminal[key] = value;
    assert.throws(() => validateTrace(badTrace, PROFILE), new RegExp(key));
  }
}
const badAudit = structuredClone(terminal);
badAudit.partnerAudit['partner-1'].actions++;
assert.throws(() => verifyTerminal(badAudit, syntheticTrace), /Partner actions\/SHA mismatch/);
const extra = { ...terminal, extra: true };
assert.throws(() => verifyTerminal(extra, syntheticTrace), /Complete terminal mismatch/);
console.log('PASS real CoopEnv.view reset/nonterminal counters, strict terminal and training compatibility');
