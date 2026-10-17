// No matches are run. The real pinned sources plus explicit fault fixtures only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {guard, verifySources, hash, repo} from './guard.mjs';
import {parseProfile, transformSource, defaultSpec, PINS} from '../policy/lib.mjs';
import {verifyRules} from '../policy/provenance.mjs';
import * as loader from '../policy/loader.mjs';

const here = fileURLToPath(new URL('./', import.meta.url));
const root = path.resolve(process.argv[2] || '../Stronghold-Protocol');
const entry = path.resolve(process.argv[3] || '../preview-tools/clean-bandbench.mjs');
const verified = await guard(root, entry);
assert.equal(verified.rules.fileCount, 486);
assert.equal(verified.defaultByteEquivalent, true);
const original = fs.readFileSync(path.join(root, PINS.botJs.relPath), 'utf8');
assert.equal(hash(original), PINS.botJs.sha256);
assert.equal(transformSource(original, defaultSpec().rules).code, original);
const values = Object.fromEntries(Object.entries(verified.schema).map(([key,s]) => [key,s.default]));
assert.equal(transformSource(original, parseProfile({...verified.baseProfile, overrides:values}).rules).code, original);
for (const [key,s] of Object.entries(verified.schema)) {
  if (s.kind === 'levels') continue;
  for (const value of [s.min, s.max]) assert.doesNotThrow(() => parseProfile({...verified.baseProfile, overrides:{[key]:value}}));
  for (const value of [s.min-1, s.max+1, NaN, Infinity]) {
    assert.throws(() => parseProfile({...verified.baseProfile, overrides:{[key]:value}}));
  }
}
assert.throws(() => parseProfile({...verified.baseProfile, overrides:{seed:1}}));
assert.throws(() => parseProfile({...verified.baseProfile, overrides:{MAX_PAIRS:1.5}}));
assert.throws(() => parseProfile({...verified.baseProfile, overrides:{LEVEL_TARGET:[6,...Array(15).fill(1)]}}));
const spec = parseProfile({...verified.baseProfile, overrides:{FOCUS_MEMBER:7,MAX_REFRESHES:3}});
const transformed = transformSource(original, spec.rules).code;
assert.equal(transformed, original.replace('const FOCUS_MEMBER = 4;', 'const FOCUS_MEMBER = 7;')
  .replace('const MAX_REFRESHES = 16;', 'const MAX_REFRESHES = 3;'));
assert.throws(() => transformSource(original.replace('const FOCUS_MEMBER = 4;', ''), spec.rules));
assert.throws(() => transformSource(original+'\nconst FOCUS_MEMBER = 4;', spec.rules));
loader.initialize({botURL:'file:///pinned/bot.js', spec});
assert.equal((await loader.load('file:///pinned/bot.js', {}, async () => ({source:Buffer.from(original),format:'module'}))).source, transformed);
assert.equal((await loader.load('file:///other.js', {}, async () => ({source:'other'}))).source, 'other');
await assert.rejects(loader.load('file:///pinned/bot.js', {}, async () => ({source:original+'\n'})), /hash mismatch/);

const tmp = fs.mkdtempSync(path.join(here, '.guard-test-'));
try {
  const wrapper = path.join(tmp,'wrapper.mjs');
  fs.copyFileSync(entry, wrapper);
  assert.deepEqual(verifySources(root, wrapper),
    Object.fromEntries(Object.entries(verified.sources).filter(([k])=>k!=='botbench')));
  fs.appendFileSync(wrapper,'\n// drift');
  assert.throws(() => verifySources(root, wrapper), /clean-bandbench/);
  for (const rel of Object.keys(verified.sources).filter(k=>k.startsWith('policy/'))) {
    fs.mkdirSync(path.dirname(path.join(tmp,rel)), {recursive:true});
    fs.copyFileSync(path.join(repo,rel),path.join(tmp,rel));
  }
  fs.appendFileSync(path.join(tmp,'policy/register.mjs'),'\n// loader drift');
  assert.throws(() => verifySources(root, entry, tmp), /policy\/register/);
  // Exact file set but invalid contents: validates content guard without copying
  // the game or ever modifying the original tree.
  const manifest = JSON.parse(fs.readFileSync(path.join(repo,'policy/pins/rules-manifest.json')));
  const fakeRoot = path.join(tmp,'game');
  for (const rel of Object.keys(manifest.files)) {
    fs.mkdirSync(path.dirname(path.join(fakeRoot,rel)), {recursive:true});
    fs.writeFileSync(path.join(fakeRoot,rel),'');
  }
  assert.throws(() => verifyRules(fakeRoot), /Rule file changed/);
  fs.writeFileSync(path.join(fakeRoot,'server/extra.js'),'');
  assert.throws(() => verifyRules(fakeRoot), /file set/);
} finally { fs.rmSync(tmp, {recursive:true, force:true}); }
console.log(JSON.stringify({status:'pass',gamesRun:0,checks:['full-source-guard','default-byte-equivalence',
  'schema-boundaries','strict-profile','only-constant-transforms','real-loader','wrapper-drift','registration-drift','rule-content-and-file-set-drift']}));
