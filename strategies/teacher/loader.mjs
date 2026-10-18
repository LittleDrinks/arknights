// One SHA guard, one composed in-memory bot transform. Never chain stacking-loader.
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { options } from './policy.mjs';
export const BOT_SHA256 = 'bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b';
const runtime = new URL('./policy.mjs', import.meta.url).href;
export function transformTeacherBot(source) {
  if (createHash('sha256').update(source).digest('hex') !== BOT_SHA256) throw new Error('teacher: bot SHA256 mismatch');
  const one = (from, to) => {
    if (source.split(from).length !== 2) throw new Error(`teacher: nonunique anchor ${from}`);
    source = source.replace(from, to);
  };
  one('  let s = power(c) * 0.6;', '  let s = power(c) * 0.6 + __teacher.priority(ps, id);');
  one('  let v = 0;\n  for (const p of set)', '  let v = __teacher.lineupBonus(ps, set, bonds);\n  for (const p of set)');
  one('  return unitBase(m, piece, ctx) + bondValue', '  return (__teacher.protectedPiece(ps, piece) ? 40 : 0) + unitBase(m, piece, ctx) + bondValue');
  one('    if (guard > 0) yield;\n    const ctx = context(m, ps);',
    '    if (guard > 0) yield;\n    yield* __teacher.buyVisibleSteps(m, ps);\n    const ctx = context(m, ps);');
  one('  sellJunk(m, ps);\n  yield;',
    "  sellJunk(m, ps);\n  yield;\n  yield* __teacher.buyVisibleSteps(m, ps);\n  yield* arrangeSteps(m, ps);\n  yield* __teacher.macroSteps(m, ps, 'begin');");
  one('  rememberOwned(ps);\n  m.autoPickPersonalChoice',
    "  yield* __teacher.buyVisibleSteps(m, ps);\n  yield* arrangeSteps(m, ps);\n  yield* __teacher.macroSteps(m, ps, 'tail');\n  resolveTemp(m, ps);\n  rememberOwned(ps);\n  m.autoPickPersonalChoice");
  // Cover every native bot sell site, including full-hand layout and temp cleanup.
  const sales = source.match(/ps\.sell\(/g) ?? [];
  if (sales.length !== 6) throw new Error(`teacher: expected 6 sell sites, found ${sales.length}`);
  source = source.replaceAll('ps.sell(', '__teacher.safeSell(ps, ');
  return `import * as __teacher from ${JSON.stringify(runtime)};\n${source}`;
}
export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (!options().on || !url.startsWith('file:')) return result;
  if (process.env.ARK_STACKING && !['off', '0', 'false'].includes(process.env.ARK_STACKING)) throw new Error('Use teacher loader alone; unset ARK_STACKING');
  const root = resolve(process.env.ARK_TEACHER_GAME_ROOT ?? fileURLToPath(new URL('../../Stronghold-Protocol/', import.meta.url)));
  if (fileURLToPath(url) !== resolve(root, 'server/match/bot.js')) return result;
  return { ...result, source: transformTeacherBot(typeof result.source === 'string' ? result.source : Buffer.from(result.source).toString('utf8')) };
}
