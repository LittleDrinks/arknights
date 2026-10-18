// policy/lib.mjs — shared pins, strict profile schema and the exact-string transform engine.
//
// Scope guard (hard rules of this experiment):
//   * The ONLY file ever modified in memory is `server/match/bot.js` of the pinned game commit. Nothing is
//     written back to disk; the game repository stays read-only. Rules, economy, enemies and RNG are untouched.
//   * Every override is an exact full-line replacement of an EXISTING bot decision constant (declared once in
//     the pinned source). No eval, no free-form code injection, no new identifiers.
//   * Ablation switches only zero out the bot's own trait SCORING census (traitsOf inputs); the real in-battle
//     trait effects live in the engine and are not touched by these regex/set constants.
//   * Every mismatch (path, sha256, occurrence count, value type/range) throws before any match is simulated.

import { createHash } from 'node:crypto';

/** Fixed rule set the pins below were taken from (game repo commit). */
export const RULES_COMMIT = '6ab2468279e926d107788f63d7fe219b85f67b4a';

export const POLICY_VERSION = '1.0.0';

/** Files this experiment pins, with the sha256 of their content at the rules commit (verified locally). */
export const PINS = {
  botJs: {
    relPath: 'server/match/bot.js',
    sha256: 'bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b',
  },
  botbench: {
    relPath: 'tools/botbench.mjs',
    sha256: '96d35d7cd9067888f147f81270733b45de4279b7a7768d3adf3ed4d409fb1d50',
  },
};

export class PolicyError extends Error {}

export function sha256(data) {
  return createHash('sha256').update(data).digest('hex');
}

// ---------------------------------------------------------------------------------------------------
// parameter specs: only constants that ALREADY exist in the pinned bot.js (each `find` must occur exactly once)
// ---------------------------------------------------------------------------------------------------

const PARAM_SPECS = {
  FOCUS_MEMBER: {
    kind: 'num', min: 0, max: 12, default: 4,
    find: 'const FOCUS_MEMBER = 4;',
    describe: 'lineup value per deployed focus-bond member (lineupScore)',
  },
  MAX_REFRESHES: {
    kind: 'int', min: 0, max: 40, default: 16,
    find: 'const MAX_REFRESHES = 16;',
    describe: 'shop refreshes per prep at most (botPrepBeginSteps → buyLoopSteps)',
  },
  MAX_PAIRS: {
    kind: 'int', min: 0, max: 8, default: 4,
    find: 'const MAX_PAIRS = 4;',
    describe: 'bench pairs held while hunting third copies (buyScore / buyLoopSteps)',
  },
  BENCH_BUDGET: {
    kind: 'int', min: 0, max: 12, default: 6,
    find: 'const BENCH_BUDGET = 6;',
    describe: 'spare bench units kept beyond the lineup (sellJunk)',
  },
  BUY_FULL_MIN: {
    kind: 'num', min: 0, max: 100, default: 8,
    find: 'const BUY_FULL_MIN = 8;',
    describe: 'least full-board buy score before the bot refreshes instead (buyLoopSteps)',
  },
  MERGE_HIT: {
    kind: 'num', min: 0, max: 200, default: 36,
    find: 'const MERGE_HIT = 36;',
    describe: 'value of a completing a merge, weighed by shop odds in refreshValue',
  },
  PAIR_HIT: {
    kind: 'num', min: 0, max: 200, default: 8,
    find: 'const PAIR_HIT = 8;',
    describe: 'value of making a keeper pair, weighed by shop odds in refreshValue',
  },
  LEVEL_TARGET: {
    kind: 'levels', min: 1, max: 6, length: 16,
    default: [1, 1, 1, 2, 2, 3, 3, 4, 4, 4, 5, 5, 6, 6, 6, 6],
    find: 'const LEVEL_TARGET = [1, 1, 1, 2, 2, 3, 3, 4, 4, 4, 5, 5, 6, 6, 6, 6];',
    describe: 'shop level aimed for at the start of round r (levelUp; index = round)',
  },
};

// Ablations: each zeroes a bot-scoring PRIOR only (bot.js traitsOf census inputs). The unit traits themselves
// (real in-battle effects) are applied by the engine from data and are NOT affected by these constants.
const ABLATION_SPECS = {
  ablateLayerPrior: {
    find: 'const LAYER_TRAIT_RE = /BOND|LAYER/;',
    replace: 'const LAYER_TRAIT_RE = /(?!)/; /* policy ablation: layer-trait buy/keep prior off (scoring only) */',
    describe: 'zero the LAYER-trait census (recurring + one-shot) used by buyScore/unitBase',
  },
  ablateRecurringLayerPrior: {
    find: "const RECURRING_TRAIT_EVENTS = new Set(['SERVER_PREP_START', 'SERVER_PREP_FIN', 'SERVER_REFRESH_SHOP']);",
    replace: "const RECURRING_TRAIT_EVENTS = new Set([]); /* policy ablation: recurring-layer prior off (scoring only) */",
    describe: 'zero only the recurring (per-prep/refresh) layer prior; one-shot SERVER_GAIN prior kept',
  },
  ablateEconPrior: {
    find: 'const ECON_TRAIT_RE = /GOLD|REFRESH|COIN/;',
    replace: 'const ECON_TRAIT_RE = /(?!)/; /* policy ablation: econ-trait buy prior off (scoring only) */',
    describe: 'zero the economy-trait (funds/refresh) buy prior',
  },
};

export const PARAM_KEYS = Object.freeze(Object.keys(PARAM_SPECS));
export const ABLATION_KEYS = Object.freeze(Object.keys(ABLATION_SPECS));

function checkNumber(key, spec, v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) {
    throw new PolicyError(`override ${key}: expected a finite number, got ${JSON.stringify(v)}`);
  }
  if (v < spec.min || v > spec.max) {
    throw new PolicyError(`override ${key}: value ${v} outside allowed range [${spec.min}, ${spec.max}]`);
  }
}

function checkValue(key, spec, v) {
  if (spec.kind === 'levels') {
    if (!Array.isArray(v) || v.length !== spec.length) {
      throw new PolicyError(`override ${key}: expected an array of ${spec.length} round targets, got ${JSON.stringify(v)}`);
    }
    for (const x of v) {
      if (!Number.isInteger(x) || x < spec.min || x > spec.max) {
        throw new PolicyError(`override ${key}: every entry must be an integer in [${spec.min}, ${spec.max}], got ${JSON.stringify(v)}`);
      }
    }
    for (let i = 1; i < v.length; i++) {
      if (v[i] < v[i - 1]) {
        throw new PolicyError(`override ${key}: must be non-decreasing over rounds, got ${JSON.stringify(v)}`);
      }
    }
    return;
  }
  if (spec.kind === 'int') {
    if (typeof v !== 'number' || !Number.isInteger(v)) {
      throw new PolicyError(`override ${key}: expected an integer, got ${JSON.stringify(v)}`);
    }
    if (v < spec.min || v > spec.max) {
      throw new PolicyError(`override ${key}: value ${v} outside allowed range [${spec.min}, ${spec.max}]`);
    }
    return;
  }
  checkNumber(key, spec, v);
}

function renderValue(key, spec, v) {
  if (spec.kind === 'levels') return `const ${key} = [${v.join(', ')}];`;
  return `const ${key} = ${String(v)};`;
}

// ---------------------------------------------------------------------------------------------------
// profile parsing (strict): unknown keys, wrong target or wrong source hash are all hard errors
// ---------------------------------------------------------------------------------------------------

/**
 * Validate a parsed profile object and normalize it into a spec:
 *   { name, description, target, sourceSha256, overrides, ablations, rules: [{ id, find, replace }] }
 * `rules` is deterministically ordered (params first, then ablations, each by key).
 */
export function parseProfile(raw) {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new PolicyError('profile: expected a JSON object');
  }
  const allowed = new Set(['name', 'description', 'target', 'sourceSha256', 'overrides', 'ablations']);
  for (const k of Object.keys(raw)) {
    if (!allowed.has(k)) throw new PolicyError(`profile: unknown top-level key "${k}"`);
  }
  if (typeof raw.name !== 'string' || !raw.name.trim() || raw.name.length > 80) {
    throw new PolicyError('profile: "name" must be a non-empty string (<= 80 chars)');
  }
  if (typeof raw.description !== 'string' || !raw.description.trim() || raw.description.length > 2000) {
    throw new PolicyError('profile: "description" must be a non-empty string (<= 2000 chars)');
  }
  if (raw.target !== PINS.botJs.relPath) {
    throw new PolicyError(`profile: "target" must be exactly "${PINS.botJs.relPath}" (got ${JSON.stringify(raw.target)}); no other file may be touched`);
  }
  if (raw.sourceSha256 !== PINS.botJs.sha256) {
    throw new PolicyError(`profile: "sourceSha256" must be the pinned bot.js hash ${PINS.botJs.sha256} (got ${JSON.stringify(raw.sourceSha256)})`);
  }
  const overrides = {};
  if (raw.overrides !== undefined) {
    if (typeof raw.overrides !== 'object' || raw.overrides === null || Array.isArray(raw.overrides)) {
      throw new PolicyError('profile: "overrides" must be an object of {PARAM: value}');
    }
    for (const [k, v] of Object.entries(raw.overrides)) {
      const spec = Object.hasOwn(PARAM_SPECS,k) ? PARAM_SPECS[k] : null;
      if (!spec) throw new PolicyError(`profile: unknown override "${k}" (allowed: ${PARAM_KEYS.join(', ')})`);
      checkValue(k, spec, v);
      overrides[k] = v;
    }
  }
  const ablations = {};
  if (raw.ablations !== undefined) {
    if (typeof raw.ablations !== 'object' || raw.ablations === null || Array.isArray(raw.ablations)) {
      throw new PolicyError('profile: "ablations" must be an object of {switch: boolean}');
    }
    for (const [k, v] of Object.entries(raw.ablations)) {
      const spec = Object.hasOwn(ABLATION_SPECS,k) ? ABLATION_SPECS[k] : null;
      if (!spec) throw new PolicyError(`profile: unknown ablation "${k}" (allowed: ${ABLATION_KEYS.join(', ')})`);
      if (typeof v !== 'boolean') throw new PolicyError(`profile: ablation "${k}" must be a boolean`);
      ablations[k] = v;
    }
  }
  const rules = [];
  for (const k of PARAM_KEYS) {
    if (k in overrides) rules.push({ id: k, find: PARAM_SPECS[k].find, replace: renderValue(k, PARAM_SPECS[k], overrides[k]) });
  }
  for (const k of ABLATION_KEYS) {
    if (ablations[k]) rules.push({ id: k, find: ABLATION_SPECS[k].find, replace: ABLATION_SPECS[k].replace });
  }
  return {
    name: raw.name,
    description: raw.description,
    target: PINS.botJs.relPath,
    sourceSha256: PINS.botJs.sha256,
    overrides,
    ablations,
    rules,
  };
}

/** Parse a profile from its JSON text. */
export function parseProfileText(text) {
  let raw;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    throw new PolicyError(`profile: invalid JSON (${e.message})`);
  }
  return parseProfile(raw);
}

/** Default spec when no profile is given: hash-verifying, strictly zero rules. */
export function defaultSpec() {
  return {
    name: 'default (env: zero-change)',
    description: 'No overrides; the pinned bot.js source is hash-verified and loaded unchanged.',
    target: PINS.botJs.relPath,
    sourceSha256: PINS.botJs.sha256,
    overrides: {},
    ablations: {},
    rules: [],
  };
}

// ---------------------------------------------------------------------------------------------------
// transform engine: exact full-line replacement, each `find` must occur EXACTLY once
// ---------------------------------------------------------------------------------------------------

function countOccurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
}

/**
 * Apply `rules` to the pinned source text. Throws unless every rule matches exactly once.
 * Returns { code, applied } where applied mirrors rules (audit trail). With zero rules the
 * output is byte-identical to the input.
 */
export function transformSource(source, rules) {
  let code = source;
  for (const rule of rules) {
    const n = countOccurrences(code, rule.find);
    if (n !== 1) {
      throw new PolicyError(`transform: "${rule.id}" replacement point found ${n} times (expected exactly 1) in the pinned source; refusing to touch anything`);
    }
    code = code.replace(rule.find, rule.replace);
  }
  if (rules.length === 0 && code !== source) {
    throw new PolicyError('transform: zero rules but the source changed — internal error');
  }
  return { code, applied: rules.map((r) => ({ id: r.id, find: r.find, replace: r.replace })) };
}

// ---------------------------------------------------------------------------------------------------
// deterministic projection of a botbench record (timings excluded) for no-op / determinism comparisons
// ---------------------------------------------------------------------------------------------------

/** The subset of a botbench per-match record that must be identical across identical seeds. */
export function projectRecord(rec) {
  return {
    config: rec.config,
    seed: rec.seed,
    stageId: rec.stageId,
    bossId: rec.bossId,
    victory: rec.victory,
    roundsPassed: rec.roundsPassed,
    hidden: rec.hidden,
    lpLeft: rec.lpLeft,
    players: rec.players,
    rounds: rec.rounds,
    bounties: rec.bounties,
    errors: rec.errors,
    errorMessages: rec.errorMessages ?? null,
    matchErrors: rec.matchErrors ?? null,
  };
}

/** Parse a botbench --json output file into an array of records. */
export function readBenchRecords(text) {
  const arr = JSON.parse(text);
  if (!Array.isArray(arr)) throw new PolicyError('bench output: expected a JSON array of match records');
  return arr;
}

/** Human-readable summary of a spec for logs / manifests. */
export function describeSpec(spec) {
  const parts = [];
  for (const [k, v] of Object.entries(spec.overrides)) parts.push(`${k}=${JSON.stringify(v)}`);
  for (const [k, v] of Object.entries(spec.ablations)) if (v) parts.push(`${k}=on`);
  return parts.length ? parts.join(' ') : '(no changes)';
}
