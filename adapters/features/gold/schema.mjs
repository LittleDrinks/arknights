// Observation-only arithmetic. No engine, pool, RNG, or terminal dependencies.
export const FEATURE_VERSION = 'shop-gold-v2';
export const BASE_VERSION = 'shop-v1';
export const AREAS = ['board', 'hand', 'temp'];

export function checkMode(mode) {
  if (mode !== 'off' && mode !== 'on') throw new Error('gold-features: expected mode off/on');
  return mode;
}

export function extendSchema(schema) {
  if (schema?.version !== BASE_VERSION || !['ids', 'bonds', 'bands'].every(k =>
    Array.isArray(schema[k]) && schema[k].every(x => typeof x === 'string'))) {
    throw new Error('gold-features: expected complete shop-v1 schema');
  }
  return { ...schema, version: FEATURE_VERSION,
    ids: [...schema.ids], bonds: [...schema.bonds], bands: [...schema.bands] };
}

export function baseSchema(schema) {
  if (schema?.version !== FEATURE_VERSION) throw new Error('gold-features: expected shop-gold-v2');
  const base = { ...schema, version: BASE_VERSION };
  extendSchema(base); // Validate without changing the caller's schema.
  return base;
}

export function goldCounts(own, schema, mode) {
  checkMode(mode);
  if (!own || !AREAS.every(a => Array.isArray(own[a]))) {
    throw new Error('gold-features: current own board/hand/temp required');
  }
  return schema.ids.flatMap(id => AREAS.map(area => mode === 'off' ? 0 :
    Math.log1p(own[area].filter(p => p?.id === id && p.kind === 'chess' && p.golden === true).length)));
}

export function appendGold(features, own, schema, mode) {
  baseSchema(schema);
  // candidates/mask retain their original references and numeric bytes.
  return { ...features, global: [...features.global, ...goldCounts(own, schema, mode)] };
}

// The caller must supply the ACTUAL snapshot captured before this sample's action.
// A timing tag is a contract, not a proof that an arbitrary snapshot is authentic.
export function convertBeforeAction(features, oldSchema, snapshot, mode) {
  if (snapshot?.timing !== 'before-action' || !snapshot.own) {
    throw new Error('gold-features: actual before-action own snapshot required; recapture old demos');
  }
  const schema = extendSchema(oldSchema);
  return { schema, features: appendGold(features, snapshot.own, schema, mode) };
}
