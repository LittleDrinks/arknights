import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { encode, makeSchema } from '../rl/features.mjs';
import { load, transform } from './loader.mjs';
import { ENV_URL, FEATURES_URL } from './pins.mjs';
import { extendSchema, appendGold, convertBeforeAction, checkMode } from './schema.mjs';
import { data, observation, candidates } from './fixtures.mjs';

const freeze = x => { if (x && typeof x === 'object') { Object.values(x).forEach(freeze); Object.freeze(x); } return x; };
const schema = freeze(makeSchema(data)), extended = freeze(extendSchema(schema));
const obs = freeze(observation()), cs = freeze(candidates);
const original = freeze(encode(obs, cs, schema));
const off = appendGold(original, obs.own, extended, 'off');
const on = appendGold(original, obs.own, extended, 'on');
const expected = [Math.log1p(2), 0, Math.log1p(1), 0, Math.log1p(1), Math.log1p(1), 0, 0, 0];
assert.deepEqual(on.global.slice(original.global.length), expected);
for (const f of [off, on]) {
  assert.equal(JSON.stringify(f.global.slice(0, original.global.length)), JSON.stringify(original.global));
  assert.equal(f.candidates, original.candidates);
  assert.equal(f.mask, original.mask);
  const bytes = a => Buffer.from(new Float64Array(a).buffer);
  assert.deepEqual(bytes(f.global.slice(0, original.global.length)), bytes(original.global));
}
assert.deepEqual(off.global.slice(original.global.length), expected.map(() => 0));
assert.deepEqual(extended.ids, ['a', 'b', 'item']);
assert.deepEqual(convertBeforeAction(original, schema, { timing: 'before-action', own: obs.own }, 'on'),
  { schema: extended, features: on });
assert.throws(() => convertBeforeAction(original, schema, { timing: 'terminal', own: obs.own }, 'on'), /before-action/);
assert.throws(() => convertBeforeAction(original, schema, undefined, 'off'), /before-action/);
for (const bad of ['', undefined, null, 'ON', 'auto']) assert.throws(() => checkMode(bad), /mode/);
const poison = { ...obs.own, get pool() { throw Error('hidden read'); }, get future() { throw Error('hidden read'); } };
assert.deepEqual(appendGold(original, poison, extended, 'on'), on);
const envSource = readFileSync(new URL(ENV_URL), 'utf8');
const anchor = "import { encode, makeSchema, pieces } from './features.mjs';";
assert.equal(transform(envSource).replace(/import \{ encode, makeSchema, pieces \} from .*?;/, anchor), envSource);
assert.throws(() => transform(envSource + '\n'), /SHA256/);

const savedMode = process.env.GOLD_FEATURES_MODE;
try {
  delete process.env.GOLD_FEATURES_MODE;
  const result = { format: 'module', source: envSource };
  assert.equal(await load(ENV_URL, {}, async () => result), result);
  for (const mode of ['off', 'on']) {
    process.env.GOLD_FEATURES_MODE = mode;
    for (const url of [ENV_URL + '?alias', new URL('../coop/env.mjs', import.meta.url).href, 'file:///other/rl/env.mjs']) {
      assert.equal(await load(url, {}, async () => result), result);
    }
    assert.equal((await load(ENV_URL, {}, async () => result)).source, transform(envSource));
    await assert.rejects(load(ENV_URL, {}, async () => ({ source: 'unknown' })), /SHA256/);
    await assert.rejects(load(FEATURES_URL, {}, async () => ({ source: 'unknown' })), /SHA256/);
  }
  process.env.GOLD_FEATURES_MODE = 'invalid';
  await assert.rejects(load(ENV_URL, {}, async () => result), /mode/);
} finally {
  if (savedMode === undefined) delete process.env.GOLD_FEATURES_MODE;
  else process.env.GOLD_FEATURES_MODE = savedMode;
}

// Exercise the REAL import hook and inherited recorder using tiny plain objects.
// Never call create/reset, import the game, or construct Match/Battle.
const probe = `
import { ShopEnv } from ${JSON.stringify(ENV_URL)};
import { data, observation, candidates } from ${JSON.stringify(new URL('./fixtures.mjs', import.meta.url).href)};
const env = new ShopEnv({ data });
const obs = observation();
env.observe = () => obs;
env.candidates = () => candidates;
env.history = obs.history; env.history.actions = [];
env.teacher = true; env.demos = []; env.steps = 0;
env.m = { round: 1, _applyBand() {}, _applyCard() {}, pickPersonalChoice() {} };
env.ps = Object.fromEntries(['buy','sell','refresh','levelUp','setReady','move','equip','pickReward','useArt','destroy','freeze']
  .map(k => [k, () => { obs.own.board[0].golden = false; return {ok:true}; }]));
env.installRecorder();
env.ps.setReady(true);
console.log(JSON.stringify({schema:env.schema, features:env.demos[0].features}));
`;
const cleanEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GOLD_FEATURES_') && !k.startsWith('NODE_')));
function run(mode, withLoader = true, source = probe, extraEnv = {}) {
  const r = spawnSync(process.execPath, ['--max-old-space-size=128', ...(withLoader ?
    ['--loader', fileURLToPath(new URL('./loader.mjs', import.meta.url))] : []), '--input-type=module', '--eval', source],
    { env: { ...cleanEnv, ...(mode === undefined ? {} : { GOLD_FEATURES_MODE: mode }), ...extraEnv },
      encoding: 'utf8', timeout: 3000, maxBuffer: 100000 });
  return r;
}
const plain = run(undefined, false), disabled = run(undefined);
assert.equal(plain.status, 0, plain.stderr);
assert.equal(disabled.status, 0, disabled.stderr);
assert.equal(plain.stdout, disabled.stdout); // Original env bytes unchanged without mode.
for (const mode of ['off', 'on']) {
  const r = run(mode);
  assert.equal(r.status, 0, r.stderr);
  const actual = JSON.parse(r.stdout);
  assert.deepEqual(actual.schema, extended);
  assert.deepEqual(actual.features, mode === 'on' ? on : off); // BEFORE synthetic action mutation.
}
assert.notEqual(run('bad').status, 0);
const temp = mkdtempSync(fileURLToPath(new URL('./.test-', import.meta.url)));
try {
  mkdirSync(temp + '/rl');
  writeFileSync(temp + '/rl/env.mjs', envSource + '\n');
  const r = run('on', true, `await import(${JSON.stringify(new URL('file://' + temp + '/rl/env.mjs').href)});`,
    { GOLD_FEATURES_ROOT: temp });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /source SHA256 mismatch/);
} finally { rmSync(temp, { recursive: true }); }
console.log('PASS: prefix bytes, own area/kind/golden counts, modes, before-action recorder, exact loader URL/SHA; synthetic only');
