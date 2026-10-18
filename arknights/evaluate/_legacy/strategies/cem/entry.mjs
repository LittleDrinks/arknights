// The only executable game entry: original diagnostic wrapper + reviewed policy
// registration. No buildloader, forced band, seed feature, or rule patch.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
import {guard, hash} from './guard.mjs';

const root = fs.realpathSync(process.env.ARK_GAME_ROOT);
const entry = fs.realpathSync(process.env.CEM_ENTRY);
const auditPath = process.env.CEM_AUDIT;
const audit = {kind:'controlled upstream bot policy experiment', trainedModel:false, status:'starting'};
const save = () => fs.writeFileSync(auditPath, JSON.stringify(audit));
try {
  for (const k of Object.keys(process.env)) {
    if ((k.startsWith('ARK_') && !['ARK_GAME_ROOT','ARK_POLICY_PROFILE'].includes(k)) ||
        ['NODE_OPTIONS','NODE_PATH','LD_PRELOAD','LD_LIBRARY_PATH'].includes(k)) throw new Error(`Unclean environment: ${k}`);
  }
  const before = await guard(root, entry);
  audit.provenance = before;
  const lib = await import('../policy/lib.mjs');
  const profile = process.env.ARK_POLICY_PROFILE;
  const spec = profile ? lib.parseProfileText(fs.readFileSync(profile, 'utf8')) : lib.defaultSpec();
  if (Object.keys(spec.ablations).length) throw new Error('CEM allows parameters only');
  audit.profile = spec;
  audit.profileFileSha256 = profile ? hash(fs.readFileSync(profile)) : null;
  const source = fs.readFileSync(`${root}/${lib.PINS.botJs.relPath}`, 'utf8');
  audit.botOriginalSha256 = hash(source);
  audit.botTransformedSha256 = hash(lib.transformSource(source, spec.rules).code);
  audit.registration = process.env.CEM_VANILLA === '1' ? 'none (smoke reference)' : 'policy/register.mjs';
  if (process.env.CEM_VANILLA === '1' && profile) throw new Error('Vanilla cannot have a profile');
  if (process.env.CEM_VANILLA !== '1') await import('../policy/register.mjs');
  audit.status = 'running'; save();
  await import(pathToFileURL(entry).href);
  const after = await guard(root, entry);
  if (JSON.stringify(after) !== JSON.stringify(before)) throw new Error('Source drift during game');
  audit.status = 'complete'; audit.sourceUnchangedAfter = true;
} catch (e) {
  audit.status = 'failed'; audit.error = String(e); process.exitCode = 1;
} finally { save(); }
