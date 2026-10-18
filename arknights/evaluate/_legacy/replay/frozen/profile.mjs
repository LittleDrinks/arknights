import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export const PROFILE = 'frozen-eval-seed65/v1';
export const KIND = 'frozen-policy-evaluation-trajectory/v1';
export const CONTROLLER = 'coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1+prep-timing/deferred-v1';
export const CHECKPOINT = 'af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b';
// Same exact P.loader used by wide-dev's original prep module, never a copied transform.
export const LOADER = new URL('../prep-timing/loader.mjs', import.meta.url);
export const LOADER_SHA256 = 'e45798398eaac83f408d849a614caa5db63c7fe7321712b0265a2607345ef44b';
export const LABEL = '冻结BC开发团队胜局(学习席R12淘汰)';
export const NOTE = '冻结 BC 开发集 seed65 评估；团队胜利 R14，学习席 R12 淘汰、个人 victory=false，三名原版队友存活至 R14。Node 仅重放已保存动作，不运行模型推理。独立 Python 推理复现见 main receipt 引用；2048 开发评估未结束，不代表整体泛化提高。四席私有视图仅用于离线终局回放。';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
export const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export function validateFrozenTrace(trace) {
  assert.equal(trace.checkpointSHA256, CHECKPOINT, 'Frozen checkpoint mismatch');
  for (const key of ['captureSourceSHA256', 'originalResultSHA256', 'reproductionManifestSHA256'])
    assert(sha(trace[key]), `Missing ${key}`);
  const t = trace.terminal, e = t.episode, p = e.partners;
  assert.equal(e.seed, 65); assert.equal(e.difficulty, 'ABYSS'); assert.equal(e.budget, 128);
  assert.equal(p.pool, 'normal'); assert.equal(p.split, 'eval'); assert.equal(p.poolSeed, 313);
  assert.deepEqual(p.fixed, ['original-v1', 'original-v1', 'original-v1']);
  assert.equal(trace.actions.length, 80); assert.equal(t.victory, true); assert.equal(t.rounds, 14);
  const players = t.summary.players;
  assert.deepEqual(players.map(p => p.playerId), ['learner', 'partner-1', 'partner-2', 'partner-3']);
  assert.equal(players[0].alive, false); assert.equal(players[0].victory, false);
  assert.equal(players[0].eliminatedRound, 12); assert.equal(players[0].roundsPassed, 11);
  for (const p of players.slice(1)) {
    assert.equal(p.alive, true); assert.equal(p.victory, true);
    assert.equal(p.eliminatedRound, null); assert.equal(p.roundsPassed, 14);
  }
  for (const key of ['partnerActionErrors', 'controllerDiagnosticErrors']) assert.equal(t[key], 0);
}

export function workerEnvironment(inherited = process.env) {
  assert.equal(hash(fs.readFileSync(LOADER)), LOADER_SHA256, 'P.loader source drift');
  const env = Object.fromEntries(Object.entries(inherited).filter(([key]) =>
    !/^(ARK_|COOP_|STRONGHOLD_|NODE_)/.test(key)));
  return { ...env, ARK_PREP_TIMING: 'deferred' };
}

export function validateOptions(options) {
  assert(sha(options.traceSha256), 'Frozen replay requires explicit --trace-sha256');
  assert(options.receipt && sha(options.receiptSha256), 'Frozen replay requires --receipt and explicit --receipt-sha256');
}

// A receipt is an attributed external claim, not a Node inference result. Hash it
// before parsing, bind it to this trace/manifest and retain the actual comparisons.
export function receiptMetadata(trace, traceSHA256, options) {
  validateOptions(options);
  assert(fs.statSync(options.receipt).size <= 1_000_000, 'Receipt exceeds 1 MB');
  const bytes = fs.readFileSync(options.receipt);
  assert.equal(hash(bytes), options.receiptSha256, 'Receipt SHA256 mismatch');
  const receipt = JSON.parse(bytes);
  assert.equal(receipt.status, 'complete'); assert.equal(receipt.inferenceRerun, true);
  assert.equal(receipt.traceSHA256, traceSHA256, 'Receipt trace mismatch');
  assert.equal(receipt.manifest.sha256, trace.reproductionManifestSHA256, 'Receipt manifest mismatch');
  assert.equal(receipt.wholeWideRunFinalized, false);
  assert.equal(receipt.source.prep.implementation['loader.mjs'].sha256, LOADER_SHA256, 'Receipt P.loader mismatch');
  const ids = ['parent-deferred-original-065', 'steps3000-deferred-original-065'];
  assert.deepEqual(receipt.comparisons.map(c => c.id), ids);
  assert.deepEqual(receipt.games.map(g => g.id), ids);
  for (const [i, id] of ids.entries()) {
    const c = receipt.comparisons[i], g = receipt.games[i];
    assert.deepEqual(c.exactPayloads, { 'actions.jsonl': true, 'protocol.jsonl': true, 'terminal.json': true });
    assert.equal(c.teamVictory, i === 1); assert.equal(c.individualVictory, false);
    assert.equal(g.status, 'complete'); assert.equal(g.eof, true); assert.equal(g.error, null);
    assert.equal(g.seed, 65); assert.equal(g.seat, 0); assert.equal(g.mode, 'coop');
    assert.equal(g.difficulty, 'ABYSS'); assert.equal(g.controller, 'deferred'); assert.equal(g.pool, 'original');
    assert.equal(g.teamVictory, i === 1); assert.equal(g.learnerIndividualVictory, false);
    assert.equal(g.rounds, i ? 14 : 13); assert.equal(g.steps, i ? 80 : 69);
    assert.deepEqual(g.learnerOutcome, c.learnerOutcome);
    assert.deepEqual(c.learnerOutcome, { alive: false, eliminatedRound: i ? 12 : 11,
      lp: 0, roundsPassed: i ? 11 : 10, victory: false });
    for (const key of ['engineErrors', 'actionErrors', 'controllerErrors', 'controllerDiagnosticErrors', 'partnerActionErrors'])
      assert.equal(g[key], 0);
    for (const file of ['actions.jsonl', 'protocol.jsonl', 'terminal.json', 'stderr.log']) {
      const evidence = receipt.evidence[id][file];
      assert(sha(evidence.sha256) && Number.isInteger(evidence.bytes) && evidence.bytes >= 0);
      assert.deepEqual(g.files[file], evidence);
    }
  }
  return { profile: PROFILE, singleCheckpoint: trace.checkpointSHA256, inferenceRerun: false,
    execution: 'saved-action-replay-only', developmentOnly: true, wholeWideRunFinalized: false,
    generalizationImprovementEstablished: false,
    learnerOutcome: receipt.comparisons[1].learnerOutcome,
    independentPythonReproduction: { attribution: 'main-supplied-receipt; raw Python inference not rerun by this exporter',
      path: path.resolve(options.receipt), sha256: options.receiptSha256, bytes: bytes.length,
      reportedInferenceRerun: receipt.inferenceRerun, manifest: receipt.manifest,
      comparisons: receipt.comparisons, evidence: receipt.evidence },
    originalResultSHA256: trace.originalResultSHA256,
    loader: { source: 'P.loader = prep-timing/loader.mjs', sha256: LOADER_SHA256, timing: 'deferred' } };
}
