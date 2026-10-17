// Exact URL + full-source SHA; off returns nextLoad's object/bytes unchanged.
import { createHash } from 'node:crypto';

export const ENV_SHA256 = '723e397fabeea4e473b65523aaadbadba7b08af7fea27ec4f29fa38c3cefc4b5';
export const ORIGINAL_VERSION = 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1';
export const VERSION = `${ORIGINAL_VERSION}+prep-timing/deferred-v1`;
export const TARGET = new URL('../rl/env.mjs', import.meta.url).href;

// No imports/controllers/candidates/forced-budget rewrites. All v1 calls use
// the existing control import and CoopEnv's capacityFilteredBot/preflight.
export const REPLACEMENTS = [
  [
    '      this.observe();\n      control(this);',
    `      this.observe();
      const { m, ps } = this;
      if (m.phase === 'PREP' && ps.alive && !ps.ready) {
        this.source = 'fixed-controller';
        try {
          m.autoPickPersonalChoice(ps, 'bot');
          // Inspect CURRENT temp after personal choice; v1 handles overflow.
          if (ps.temp.some(Boolean)) control(this);
        } finally { this.source = 'policy'; }
      }`,
  ],
  [
    "    const result = this.ps[a.type === 'ready' ? 'setReady' : a.type](...args);",
    `    // cs/a were selected before control. Never regenerate candidates here.
    if (a.type === 'ready') {
      control(this);
      if (!this.ps.privateView().canReady) {
        this.actionErrors++;
        throw new Error('PREP_TIMING_READY_INVALID: canReady false after v1 control');
      }
    }
    const result = this.ps[a.type === 'ready' ? 'setReady' : a.type](...args);`,
  ],
  [`controller: '${ORIGINAL_VERSION}'`, `controller: '${VERSION}'`],
];

function check(source) {
  if (createHash('sha256').update(source).digest('hex') !== ENV_SHA256) {
    throw new Error('prep-timing: env SHA256 mismatch');
  }
}

export function transform(source) {
  check(source);
  for (const [from, to] of REPLACEMENTS) {
    if (source.split(from).length !== 2) throw new Error('prep-timing: nonunique anchor');
    source = source.replace(from, to);
  }
  return source;
}

export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (url !== TARGET) return result;
  const timing = process.env.ARK_PREP_TIMING;
  if (!['off', 'deferred'].includes(timing)) throw new Error('prep-timing: require off or deferred');
  check(result.source); // Even off rejects a drifted target; successful off is byte-identical.
  if (timing === 'off') return result;
  const source = typeof result.source === 'string' ? result.source : Buffer.from(result.source).toString('utf8');
  return { ...result, source: transform(source) };
}
