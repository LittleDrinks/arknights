import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { gzipSync, gunzipSync } from 'node:zlib';
import { DEFAULT_RAW, DEFAULT_REFERENCE, ROOT, readJsonl, validateRaw, sha, writeNewDirectory } from './extract.mjs';

test('full raw: primary68 exact, survivor-only BC contract, fail-closed reference, safe output, bad tails', () => {
  const temp = fs.mkdtempSync(path.join(ROOT, 'winning-labels/.test-'));
  const cli = path.join(ROOT, 'winning-labels/extract.mjs');
  const args = ['--max-old-space-size=512', cli];
  const run = extra => execFileSync(process.execPath, [...args, ...extra], { encoding: 'utf8', timeout: 20_000 });
  const hashes = [DEFAULT_RAW, DEFAULT_REFERENCE].map(f => sha(fs.readFileSync(f)));
  try {
    const output = path.join(temp, 'bundle');
    const audit = JSON.parse(run(['--out', output]));
    assert.equal(audit.primaryExact, 68); assert.equal(audit.samples, 207);
    assert.equal(audit.seats['partner-1'].samples, 93); assert.equal(audit.seats['partner-2'].samples, 114);
    assert.equal(audit.seats.learner.samples, 0); assert.equal(audit.seats['partner-3'].samples, 0);
    assert.equal(audit.transitions, 1066); assert.equal(audit.independentEpisodes, 1);
    assert(audit.maxRSSMiB <= 768); assert(audit.elapsedMs < 20_000); assert(audit.outputBytes <= 5_000_000);
    const samples = readJsonl(path.join(output, 'samples.jsonl.gz')).rows;
    const labels = readJsonl(path.join(output, 'labels.jsonl.gz')).rows;
    const dispositions = readJsonl(path.join(output, 'dispositions.jsonl.gz')).rows;
    const meta = JSON.parse(gunzipSync(fs.readFileSync(path.join(output, 'metadata.json.gz'))));
    assert.equal(dispositions.length, 1066); assert.equal(meta.positiveSeatSequences, 2);
    assert.equal(meta.terminal.summary.players[2].stats.bossDamage, 6163482);
    assert.equal(meta.terminal.summary.players[2].lineup.filter(p => p.golden).length, 6);
    assert.equal(meta.schema.version, 'shop-v1');
    for (const [i, s] of samples.entries()) {
      assert.deepEqual(Object.keys(s).sort(), ['action', 'features']);
      const { features: f, action: a } = s, label = labels[i];
      // This is the unchanged bc_update input contract; deliberately no torch or training.
      assert(Number.isInteger(a) && a >= 0 && a < f.mask.length && f.mask[a]);
      assert.equal(f.global.length, 5267); assert.equal(f.candidates.length, f.mask.length);
      assert(f.global.every(Number.isFinite)); assert(f.candidates.every(c => c.length === 419 && c.every(Number.isFinite)));
      assert(['partner-1', 'partner-2'].includes(label.playerId));
      assert.equal(label.sample, i); assert.equal(label.candidateIndex, a);
      assert.deepEqual(label.candidate, label.candidates[a]);
      assert.equal(label.sampleSHA256, sha(JSON.stringify(s)));
      assert.equal(label.candidateSHA256, sha(JSON.stringify(label.candidates)));
      assert.equal(dispositions[label.seq].accepted, true);
      assert.equal(dispositions[label.seq].selection, 'positive-label');
    }
    for (const id of ['partner-1', 'partner-2']) assert.equal(Math.max(...labels.filter(l => l.playerId === id).map(l => l.round)), 14);
    const manifest = JSON.parse(fs.readFileSync(path.join(output, 'audit.json')));
    for (const [name, record] of Object.entries(manifest.files)) {
      const b = fs.readFileSync(path.join(output, name)); assert.equal(b.length, record.bytes); assert.equal(sha(b), record.sha256);
    }
    const before = fs.readdirSync(output).map(name => [name, sha(fs.readFileSync(path.join(output, name)))]);
    const conflict = spawnSync(process.execPath, [...args, '--out', output], { encoding: 'utf8' });
    assert.notEqual(conflict.status, 0); assert.match(conflict.stderr, /no overwrite/);
    assert.deepEqual(fs.readdirSync(output).map(name => [name, sha(fs.readFileSync(path.join(output, name)))]), before);
    assert.throws(() => writeNewDirectory(path.join(ROOT, 'outside-winning-labels'), new Map()), /inside winning-labels/);
    const link = path.join(temp, 'outside-link'); fs.symlinkSync(ROOT, link, 'dir');
    assert.throws(() => writeNewDirectory(path.join(link, 'outside-winning-labels'), new Map()), /inside winning-labels/);
    const childrenBeforeAudit = fs.readdirSync(temp);
    assert.equal(JSON.parse(run(['--audit'])).output, null);
    assert.deepEqual(fs.readdirSync(temp), childrenBeforeAudit);

    // A wrong exact-reference is a hard failure BEFORE creating an output directory.
    const ref = readJsonl(DEFAULT_REFERENCE).rows; ref[0].features.global[0] += 1;
    const wrong = path.join(temp, 'wrong-reference.gz');
    fs.writeFileSync(wrong, gzipSync(ref.map(r => JSON.stringify(r)).join('\n') + '\n'));
    const failedOutput = path.join(temp, 'must-not-exist');
    const failure = spawnSync(process.execPath, [...args, '--reference', wrong, '--out', failedOutput], { encoding: 'utf8', timeout: 20_000 });
    assert.notEqual(failure.status, 0);
    assert.match(failure.stderr, /primary step=0 seq=4.*first difference.*global.*actual.*expected/);
    assert(!fs.existsSync(failedOutput));

    // Validate full terminal and the late tail; do not cut the capture at the primary's death.
    const raw = readJsonl(DEFAULT_RAW).rows;
    const valid = validateRaw(raw); assert.equal(valid.events.length, 1066);
    const terminal = raw.at(-1), last = raw.at(-2);
    terminal.summary.errors = 1; assert.throws(() => validateRaw(raw)); terminal.summary.errors = 0;
    last.exception = 'late failure after survivor ready'; assert.throws(() => validateRaw(raw), /exception at seq/); delete last.exception;
    assert.throws(() => validateRaw(raw.slice(0, -1)), /terminal/);
    const died = terminal.summary.players[0]; died.victory = true;
    assert.throws(() => validateRaw(raw)); died.victory = false;
    const original = last.next_observation.own.playerId; last.next_observation.own.playerId = 'learner';
    assert.throws(() => validateRaw(raw), /private seat mismatch/); last.next_observation.own.playerId = original;
    assert.deepEqual([DEFAULT_RAW, DEFAULT_REFERENCE].map(f => sha(fs.readFileSync(f))), hashes);
  } finally {
    fs.rmSync(temp, { recursive: true }); // Only this test's freshly created directory.
  }
});
