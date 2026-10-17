// Optional ESM source transform: only bot.js, only the pinned bytes, never disk writes.
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
export const BOT_SHA256 = 'bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b';
export const BEGIN_ANCHOR = '  sellJunk(m, ps);\n  yield;\n  yield* buyLoopSteps(m, ps, { fillOnly: true, maxRefreshes: 0 });';
export const END_ANCHOR = 'export function* botPrepEndSteps(m, ps, job = null) {\n  if (!ps.alive || ps.ready) return;';
const runtime = new URL('./stacking-runtime.mjs', import.meta.url).href;
const isOn = () => !['', '0', 'off', 'false'].includes((process.env.ARK_STACKING ?? '').trim().toLowerCase());

export function transformStackingBot(source) {
  if (createHash('sha256').update(source).digest('hex') !== BOT_SHA256) throw new Error('stacking-loader: bot.js SHA256 mismatch');
  for (const anchor of [BEGIN_ANCHOR, END_ANCHOR]) {
    if (source.split(anchor).length !== 2) throw new Error('stacking-loader: expected exactly one anchor');
  }
  return `import { stackingPrepSteps as __arkStackingPrepSteps } from ${JSON.stringify(runtime)};\n` + source
    .replace(BEGIN_ANCHOR, BEGIN_ANCHOR.replace('  yield* buyLoopSteps', "  yield* __arkStackingPrepSteps(m, ps, 'begin');\n  yield* buyLoopSteps"))
    .replace(END_ANCHOR, END_ANCHOR + "\n  yield* __arkStackingPrepSteps(m, ps, 'tail');");
}

export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (!isOn() || !url.startsWith('file:')) return result;
  const root = resolve(process.env.ARK_STACKING_GAME_ROOT ?? fileURLToPath(new URL('../../Stronghold-Protocol/', import.meta.url)));
  if (fileURLToPath(url) !== resolve(root, 'server/match/bot.js')) return result;
  const source = typeof result.source === 'string' ? result.source : Buffer.from(result.source).toString('utf8');
  return { ...result, source: transformStackingBot(source) };
}
