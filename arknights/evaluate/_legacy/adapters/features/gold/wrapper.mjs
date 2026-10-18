import { readFileSync } from 'node:fs';
import { FEATURES_URL, FEATURES_SHA256, checkSource } from './pins.mjs';
import { checkMode, extendSchema, baseSchema, appendGold } from './schema.mjs';

const mode = checkMode(process.env.GOLD_FEATURES_MODE);
checkSource(readFileSync(new URL(FEATURES_URL)), FEATURES_SHA256);
const original = await import(FEATURES_URL);
export const pieces = original.pieces;
export const makeSchema = data => extendSchema(original.makeSchema(data));
export function encode(observation, candidates, schema) {
  const features = original.encode(observation, candidates, baseSchema(schema));
  return appendGold(features, observation.own, schema, mode);
}
