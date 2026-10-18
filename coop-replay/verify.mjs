import assert from 'node:assert/strict';
import { RULES } from '../rl/env.mjs';
import { MANIFEST, MANIFEST_SHA256, poolConfig, selectTeam } from '../coop/pool.mjs';
import { profileFor } from './profiles.mjs';
import { PROFILE, validateFrozenTrace } from '../frozen-replay/profile.mjs';
export { verifyCandidate } from '../model-replay/replay.mjs';

export const KIND = 'actual-training-trajectory-not-frozen-model-evaluation';
export const CONTROLLER = 'coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1';
export const TRACE_SHA256 = 'a1b9970568b885fb48b4a9c6797ee455635f47f5471b57cff7de83adb8e0e651';

export function configuration(trace) {
  const p = trace.terminal.episode.partners;
  const config = poolConfig({ COOP_POOL: p.pool, COOP_SPLIT: p.split, COOP_POOL_SEED: String(p.poolSeed),
    ...(p.fixed === null ? {} : { COOP_TEAM: p.fixed.join(',') }) });
  assert.deepEqual(p, { manifest: MANIFEST.version, manifestSHA256: MANIFEST_SHA256, ...config,
    seats: selectTeam(config, trace.terminal.episode.seed).map((m, i) => ({ seat: i + 1, id: m.id, family: m.family })) },
  'Partner configuration/manifest/seat mismatch');
  return config;
}

export function validateTrace(trace, profile) {
  const selected = profileFor(profile);
  assert.equal(trace.kind, selected.kind);
  assert.equal(trace.rules, RULES);
  assert.equal(trace.controller, selected.controller);
  const t = trace.terminal, e = t.episode;
  assert.equal(t.ok, true);
  assert.equal(e.mode, 'coop');
  assert(Number.isInteger(e.seed) && e.seed > 0 && e.seed <= 0xffffffff);
  assert(['NORMAL', 'HARD', 'ABYSS'].includes(e.difficulty));
  assert(Number.isInteger(trace.budget) && trace.budget >= 1 && trace.budget <= 1024);
  assert.equal(e.budget, trace.budget);
  assert(Array.isArray(trace.actions) && trace.actions.length > 0 && trace.actions.length <= 100000);
  assert.equal(t.steps, trace.actions.length);
  for (const a of trace.actions) {
    assert(Number.isInteger(a.round) && a.round >= 1);
    assert(Number.isInteger(a.candidateIndex) && a.candidateIndex >= 0);
    assert(['buy', 'sell', 'refresh', 'levelUp', 'ready'].includes(a.action?.type));
  }
  configuration(trace);
  verifyState(t, trace, profile);
  assert.equal(t.done, true);
  assert.equal(t.summary.players.length, 4);
  assert.equal(t.summary.errors, 0);
  assert.equal(t.summary.victory, t.victory);
  assert.equal(t.summary.roundsPassed, t.rounds);
  assert.equal(t.reward, t.victory ? 1 : -1);
  for (const id of ['partner-1', 'partner-2', 'partner-3'])
    assert(/^[a-f0-9]{64}$/.test(t.partnerAudit[id].actionSHA256));
  if (selected.id === PROFILE) validateFrozenTrace(trace);
  return trace;
}

export function verifyState(state, trace, profile, env) {
  assert.equal(state.rules, RULES, 'Rules drift');
  assert.equal(state.controller, profileFor(profile).controller, 'Controller drift');
  const { diagnostics: _actual, ...actual } = state.episode;
  const { diagnostics: _expected, ...expected } = trace.terminal.episode;
  assert.deepEqual(actual, expected, 'Episode configuration drift');
  // This particular trace has no diagnostic rejections either. Never broaden acceptance.
  for (const k of ['engineErrors', 'actionErrors', 'controllerErrors']) assert.equal(state[k], 0, k);
  if (profileFor(profile).id === PROFILE) {
    for (const k of ['partnerActionErrors', 'controllerDiagnosticErrors']) {
      // CoopEnv exposes these in views only at terminal. Replay must check the
      // live counters after reset/every step; absence is never evidence of zero.
      if (env !== undefined || !state.done) assert.equal(env?.[k], 0, `env.${k}`);
      // Static trace validation has no env. Terminal fields remain mandatory,
      // including when live counters were supplied and passed above.
      if (state.done) assert.equal(state[k], 0, k);
    }
  }
}

export function verifyTerminal(state, trace) {
  assert.equal(state.done, true, 'Trace ended before terminal');
  const { ok, ...expected } = trace.terminal; // Only the transport envelope is not an env field.
  assert.deepEqual(state.summary, expected.summary, 'Complete summary mismatch');
  assert.deepEqual(state.partnerAudit, expected.partnerAudit, 'Partner actions/SHA mismatch');
  assert.deepEqual(state, expected, 'Complete terminal mismatch');
}
