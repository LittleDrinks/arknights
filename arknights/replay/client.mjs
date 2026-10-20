import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { RULES } from './export.mjs';
import { siteCsp } from '../../game/server/http/csp.js';

// Only content-addressed, verified build outputs get long-lived caching. Mutable records never do.
export function clientBuild(root) {
  const file = path.join(root, 'runtime/web/client.json');
  if (!fs.existsSync(file)) return null;
  const value = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (value.version !== 1 || value.rules !== RULES || !value.index || !value.collect) throw Error('Invalid browser build');
  for (const [name, expected] of Object.entries(value.files)) {
    if (name !== path.basename(name) || !/^[\w.-]+\.(js|css)$/.test(name)) throw Error('Invalid browser build path');
    const bytes = fs.readFileSync(path.join(root, 'runtime/web/client', name));
    if (createHash('sha256').update(bytes).digest('hex') !== expected) throw Error('Browser build changed: ' + name);
  }
  for (const [name, expected] of Object.entries(value.inputs)) {
    if (path.isAbsolute(name) || name.split(path.sep).includes('..')) throw Error('Invalid browser input path');
    const bytes = fs.readFileSync(path.join(root, name));
    if (createHash('sha256').update(bytes).digest('hex') !== expected) throw Error('Browser build is stale: ' + name);
  }
  const hashes = [...(value.index + value.collect).matchAll(/<script\s+type=["']importmap["']\s*>([\s\S]*?)<\/script>/g)]
    .map(m => `'sha256-${createHash('sha256').update(m[1]).digest('base64')}'`);
  value.csp = siteCsp(path.join(root, 'game/public')).replace("script-src 'self'", "script-src 'self' " + [...new Set(hashes)].join(' '));
  return value;
}
