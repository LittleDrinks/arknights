import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const ENV_SHA256 = '723e397fabeea4e473b65523aaadbadba7b08af7fea27ec4f29fa38c3cefc4b5';
export const FEATURES_SHA256 = '78f6c7a8bf99d35b6feb858536dc2d33fc50f36e6626ef9ab55d8903de9499da';
export const ROOT = resolve(process.env.GOLD_FEATURES_ROOT ?? fileURLToPath(new URL('../', import.meta.url)));
export const ENV_URL = pathToFileURL(resolve(ROOT, 'rl/env.mjs')).href;
export const FEATURES_URL = pathToFileURL(resolve(ROOT, 'rl/features.mjs')).href;
export function checkSource(source, expected) {
  if (createHash('sha256').update(source).digest('hex') !== expected) {
    throw new Error('gold-features: source SHA256 mismatch');
  }
}
