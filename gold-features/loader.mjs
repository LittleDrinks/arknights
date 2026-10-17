// Only this exact URL and pinned import are transformed; no disk writes.
import { ENV_URL, ENV_SHA256, FEATURES_URL, FEATURES_SHA256, checkSource } from './pins.mjs';
import { checkMode } from './schema.mjs';
const wrapper = new URL('./wrapper.mjs', import.meta.url).href;
const anchor = "import { encode, makeSchema, pieces } from './features.mjs';";

export function transform(source) {
  checkSource(source, ENV_SHA256);
  if (source.split(anchor).length !== 2) throw new Error('gold-features: import anchor mismatch');
  return source.replace(anchor, `import { encode, makeSchema, pieces } from ${JSON.stringify(wrapper)};`);
}

export async function load(url, context, nextLoad) {
  const mode = process.env.GOLD_FEATURES_MODE;
  if (mode !== undefined) checkMode(mode);
  const result = await nextLoad(url, context);
  if (mode === undefined) return result;
  if (url === FEATURES_URL) checkSource(result.source, FEATURES_SHA256);
  if (url !== ENV_URL) return result;
  return { ...result, source: transform(typeof result.source === 'string' ? result.source :
    Buffer.from(result.source).toString('utf8')) };
}
