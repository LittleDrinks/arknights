// Offline review of the two preserved real games. Does NOT simulate or repair
// the original failed run status. Invoke after the no-game contract tests.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {guard, hash, repo} from './guard.mjs';
import {projectRecord} from '../policy/lib.mjs';

const here = fileURLToPath(new URL('./', import.meta.url));
const folder = path.join(here,'evidence/smoke');
const manifest = JSON.parse(fs.readFileSync(path.join(folder,'manifest.json')));
const source = await guard(path.resolve(process.argv[2] || '../Stronghold-Protocol'),
  path.resolve(process.argv[3] || '../preview-tools/clean-bandbench.mjs'));
assert.deepEqual(source, manifest.sourceGuardBefore);
assert.deepEqual(source, manifest.sourceGuardAfter);
assert.equal(manifest.denominator, 2);
assert.equal(manifest.status, 'failed');
assert.equal(manifest.resourceError, 'Local RSS bound exceeded');
assert.equal(manifest.smokeEquivalence.equal, true);
assert.equal(manifest.resources.cleanupVerified, true);
assert.equal(manifest.resources.liveProcessGroups.length, 0);
assert.ok(manifest.resources.simEnvelopeSeconds < 8);
assert.ok(manifest.resources.peakSampledAggregateRssBytes < 512*1024**2);
assert.equal(hash(fs.readFileSync(path.join(here,'evidence/smoke-runner-before-resource-fix.py.txt'))),
  manifest.cemSourceSha256['run.py']);
const records = [];
for (const row of manifest.games) {
  assert.equal(row.status,'complete');
  assert.equal(row.cleanup,true);
  const raw = fs.readFileSync(path.join(folder,row.raw));
  assert.equal(hash(raw),row.rawSha256);
  const parsed = JSON.parse(raw);
  assert.equal(parsed.length,1);
  const rec = parsed[0];
  assert.equal(rec.config,'solo:ABYSS');
  assert.equal(rec.seed,20017);
  assert.equal(rec.errors,0);
  assert.equal(rec.victory,false);
  assert.equal(rec.roundsPassed,10);
  assert.equal(rec.finalDiagnostics.reason,'eliminated');
  assert.equal(rec.finalDiagnostics.bossPool,null);
  const auditData = fs.readFileSync(path.join(folder,row.raw.replace(/\.json$/,'.audit.json')));
  assert.equal(hash(auditData),row.auditSha256);
  const audit = JSON.parse(auditData);
  assert.equal(audit.status,'complete');
  assert.equal(audit.trainedModel,false);
  assert.equal(audit.botOriginalSha256,audit.botTransformedSha256);
  assert.deepEqual(audit.profile.rules,[]);
  assert.deepEqual(audit.provenance,source);
  assert.equal(audit.sourceUnchangedAfter,true);
  const expectedRegistration = row.candidate==='vanilla' ? 'none (smoke reference)' : 'policy/register.mjs';
  assert.equal(audit.registration,expectedRegistration);
  records.push(rec);
}
assert.equal(records.length,2);
assert.deepEqual(projectRecord(records[0]),projectRecord(records[1]));
assert.deepEqual(records[0].finalDiagnostics,records[1].finalDiagnostics);
const report = {
  kind:'offline verification, no games run', realGamesEverRunForThisTask:2,
  originalRunStatus:manifest.status, originalRunStatusPreserved:true,
  defaultRuntimeEquivalence:true, naturalTerminalLossesRetained:true,
  fullPlanDenominator:2, completeNaturalGames:2, victories:0,
  trainedCEM:false, devEvaluated:false, improvementClaim:false,
  rules:source.rules, sources:source.sources,
  simulationEnvelopeSeconds:manifest.resources.simEnvelopeSeconds,
  peakSampledAggregateRssBytes:manifest.resources.peakSampledAggregateRssBytes,
  maxReapedGameChildRssBytes:manifest.resources.maxReapedChildRssBytes,
  cleanupVerified:true,
  limitation:'Original run failed only the legacy resource upper-bound check. SELF ru_maxrss inherits pre-exec launcher history. Current /proc VmHWM fix is regression-tested without simulation. Original parent VmHWM was not recorded; no corrected retrospective hard RSS bound or post-fix game run is claimed.',
  originalRunnerSha256:manifest.cemSourceSha256['run.py'],
  correctedRunnerSha256:hash(fs.readFileSync(path.join(repo,'cem/run.py'))),
  rawSha256:manifest.games.map(r=>({file:r.raw,sha256:r.rawSha256})),
};
fs.writeFileSync(path.join(here,'evidence/verification.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({offlineVerified:true,defaultRuntimeEquivalence:true,originalRunStatus:'failed',gamesRunNow:0}));
