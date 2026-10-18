// One exact-file transform; default off returns nextLoad's result by identity.
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { options } from './policy.mjs';
export const BOT_SHA256 = 'bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b';
const runtime = new URL('./policy.mjs', import.meta.url).href;
export function transform(source) {
  if (createHash('sha256').update(source).digest('hex') !== BOT_SHA256) throw new Error('build-teacher: bot SHA256 mismatch');
  const one = (from, to) => {
    if (source.split(from).length !== 2) throw new Error(`build-teacher: nonunique anchor ${from}`);
    source = source.replace(from, to);
  };
  one('  let s = power(c) * 0.6;', '  let s = power(c) * 0.6 + __build.buyBonus(ps, id);');
  one('  let v = 0;\n  for (const p of set)', '  let v = __build.lineupBonus(ps, set, bonds);\n  for (const p of set)');
  one('  return unitBase(m, piece, ctx) + bondValue', '  return __build.protection(ps, piece) + unitBase(m, piece, ctx) + bondValue');
  one('Math.min(0.9, (pool.supply.get(id) || 0) / 100);', 'Math.min(0.9, (pool.supply.get(id) || 0) / 100) + __build.focusBonus(ps, id);');
  one('    s += m.rngBots() * 0.5;', '    s += __build.cardBonus(ps, c);\n    s += m.rngBots() * 0.5;');
  one('const sc = s.kind ===', 'const sc = __build.cardBonus(ps, s) + (s.kind ===');
  one(': buyScore(m, ps, s.id, ctx);', ': buyScore(m, ps, s.id, ctx));');
  one('sc = 6 + (gd.tierOf(s.id)', 'sc = __build.itemBonus(ps, s.id) + 6 + (gd.tierOf(s.id)');
  one('  const key = itemEffect(rec);\n  const value = new Map();',
    '  const preferred = __build.equipmentTarget(ps, item);\n  if (preferred !== undefined) return preferred;\n  const key = itemEffect(rec);\n  const value = new Map();');
  one('const free = (p) => (p.items || []).length < gd.equipPerChess;',
    'const free = (p) => (p.items || []).length < gd.equipPerChess && !__build.reserved(ps, p, item);');
  one('        const v = value + noise;',
    '        const v = value + noise + __build.placementBonus(ps, p, k, out, pieces, value, u.cover);');
  one('  if (!ps.tempEmpty) ps.resolveTemp();', '  __build.finishTemp(ps);');
  const sales = source.match(/ps\.sell\(/g) ?? [];
  if (sales.length !== 6) throw new Error('build-teacher: expected six sell sites');
  source = source.replaceAll('ps.sell(', '__build.safeSell(ps, ');
  return `import * as __build from ${JSON.stringify(runtime)};\n${source}`;
}
export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (!options().on || !url.startsWith('file:')) return result;
  const root = resolve(process.env.ARK_BUILD_GAME_ROOT ?? fileURLToPath(new URL('../../Stronghold-Protocol/', import.meta.url)));
  if (fileURLToPath(url) !== resolve(root, 'server/match/bot.js')) return result;
  for (const key of ['ARK_TEACHER', 'ARK_STACKING', 'ARK_POLICY', 'ARK_ITEM_CONTROLLER']) {
    if (process.env[key] && !['off', '0', 'false'].includes(process.env[key])) throw new Error(`build-teacher: incompatible ${key}`);
  }
  return { ...result, source: transform(typeof result.source === 'string' ? result.source : Buffer.from(result.source).toString('utf8')) };
}
