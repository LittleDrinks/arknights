// No game imports here. Verify trusted sources BEFORE importing policy code.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';

export const repo = fileURLToPath(new URL('../', import.meta.url));
const pins = JSON.parse(fs.readFileSync(new URL('./pins.json', import.meta.url)));
export const hash = data => createHash('sha256').update(data).digest('hex');
export function verifySources(root, entry, policyRoot = repo) {
  const sources = {};
  for (const [rel, expected] of Object.entries(pins)) {
    if (!rel.startsWith('policy/')) continue;
    const actual = hash(fs.readFileSync(path.join(policyRoot, rel)));
    if (actual !== expected) throw new Error(`Source drift: ${rel}`);
    sources[rel] = actual;
  }
  sources.entry = hash(fs.readFileSync(entry));
  if (sources.entry !== pins.entrySha256) throw new Error('Source drift: clean-bandbench');
  return sources;
}

// PARAM_SPECS is private in the reviewed module. Read its pinned literal without
// eval or a second handwritten range table; parseProfile remains the authority.
export function readSchema(source, keys) {
  const block = source.split('const PARAM_SPECS = {\n')[1]?.split('\n};')[0];
  if (!block) throw new Error('PARAM_SPECS anchor mismatch');
  const schema = {};
  for (const key of keys) {
    const body = block.match(new RegExp(`  ${key}: \\{([\\s\\S]*?)\\n  \\}`))?.[1];
    const kind = body?.match(/kind: '(num|int|levels)'/)?.[1];
    const min = Number(body?.match(/min: (\d+)/)?.[1]);
    const max = Number(body?.match(/max: (\d+)/)?.[1]);
    const raw = body?.match(/default: (\[[^\]]+\]|[\d.]+)/)?.[1];
    if (!kind || !Number.isFinite(min) || !Number.isFinite(max) || !raw) throw new Error(`Schema anchor: ${key}`);
    schema[key] = {kind, min, max, default: JSON.parse(raw)};
    if (kind === 'levels') schema[key].length = Number(body.match(/length: (\d+)/)[1]);
  }
  return schema;
}

export async function guard(root, entry) {
  const sources = verifySources(root, entry);
  const lib = await import('../policy/lib.mjs');
  const {verifyRules} = await import('../policy/provenance.mjs');
  const rules = verifyRules(root);
  sources.botbench = hash(fs.readFileSync(path.join(root, lib.PINS.botbench.relPath)));
  if (sources.botbench !== lib.PINS.botbench.sha256) throw new Error('Source drift: botbench');
  const schema = readSchema(fs.readFileSync(path.join(repo, 'policy/lib.mjs'), 'utf8'), lib.PARAM_KEYS);
  const baseProfile = {name:'cem-default', description:'Reviewed upstream constants; no rule changes.',
    target:lib.PINS.botJs.relPath, sourceSha256:lib.PINS.botJs.sha256, overrides:{}, ablations:{}};
  const defaults = Object.fromEntries(Object.entries(schema).map(([k,s]) => [k,s.default]));
  const original = fs.readFileSync(path.join(root, lib.PINS.botJs.relPath), 'utf8');
  const noOp = lib.transformSource(original, lib.defaultSpec().rules).code;
  const explicit = lib.transformSource(original, lib.parseProfile({...baseProfile, overrides:defaults}).rules).code;
  if (original !== noOp || original !== explicit) throw new Error('Default source equivalence failed');
  return {baseCommit:pins.baseCommit, nodeVersion:process.version, sources, rules, schema, baseProfile, defaultByteEquivalent:true};
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  try { console.log(JSON.stringify(await guard(fs.realpathSync(process.argv[2]), fs.realpathSync(process.argv[3])))); }
  catch (e) { console.error(e.message); process.exitCode = 1; }
}
