// Exact-file, fail-closed in-memory ablation. Off returns nextLoad's result unchanged.
import { createHash } from 'node:crypto';
export const ENV_SHA256 = '723e397fabeea4e473b65523aaadbadba7b08af7fea27ec4f29fa38c3cefc4b5';
export const VERSION = 'upstream-drafts+no-sale-arrange+visible-items+free-rewards/v2';
const target = new URL('../rl/env.mjs', import.meta.url).href;
const controller = new URL('./control.mjs', import.meta.url).href;
export function transform(source) {
  if (createHash('sha256').update(source).digest('hex') !== ENV_SHA256) throw new Error('controller-v2: env SHA256 mismatch');
  for (const [from, to] of [
    ["import { control } from './controller.mjs';", `import { control } from ${JSON.stringify(controller)};`],
    ["controller: 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1'", `controller: '${VERSION}'`],
  ]) {
    if (source.split(from).length !== 2) throw new Error('controller-v2: nonunique anchor');
    source = source.replace(from, to);
  }
  return source;
}
export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (process.env.ARK_ITEM_CONTROLLER !== 'v2' || url !== target) return result;
  return { ...result, source: transform(typeof result.source === 'string' ? result.source : Buffer.from(result.source).toString('utf8')) };
}
