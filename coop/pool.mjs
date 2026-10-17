import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const identities = {
  'original-v1': ['upstream-bot', 'train', 'original'],
  'buy2-v1': ['first-offer-buy2', 'train', 'buy2'],
  'saver-v1': ['level-first-saver', 'train', 'saver'],
  'random-v1': ['random-legal', 'heldout', 'random'],
};
const pools = { normal: ['original-v1'], mixed: ['original-v1', 'buy2-v1', 'saver-v1'],
  weak: ['buy2-v1', 'saver-v1'], heldout: ['random-v1'] };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const keys = (o, expected) => same(Object.keys(o).sort(), expected.sort());
export function validateManifest(m) {
  if (!m || !keys(m, ['version', 'kind', 'members', 'pools']) || m.version !== 'coop-heuristics-v1' ||
      m.kind !== 'frozen-heuristics-not-FCP-checkpoints' || !Array.isArray(m.members) ||
      m.members.length !== Object.keys(identities).length) throw new Error('Invalid heuristic manifest');
  const seen = new Set();
  for (const x of m.members) {
    if (!keys(x, ['id', 'family', 'split', 'strategy']) || seen.has(x.id) ||
        !same(identities[x.id], [x.family, x.split, x.strategy])) throw new Error('Invalid manifest family identity');
    seen.add(x.id);
  }
  if (!keys(m.pools, Object.keys(pools)) || Object.keys(pools).some(k => !same(m.pools[k], pools[k]))) {
    throw new Error('Invalid manifest pool/split');
  }
  return m;
}
function freeze(x) { if (x && typeof x === 'object') { Object.values(x).forEach(freeze); Object.freeze(x); } return x; }
export const MANIFEST = freeze(validateManifest(JSON.parse(readFileSync(new URL('./pool.json', import.meta.url), 'utf8'))));
export const MANIFEST_SHA256 = createHash('sha256').update(JSON.stringify(MANIFEST)).digest('hex');
export function uint32(n, name) {
  if (!Number.isInteger(n) || n < 1 || n > 0xffffffff) throw new Error(`Invalid ${name}: expected uint32 in 1..4294967295`);
  return n;
}
// Domain-separated local state. Never receives a Match or calls m.rng*.
export function localRng(seed, domain) {
  let state = createHash('sha256').update(`${seed}/${domain}`).digest().readUInt32LE(0) || 1;
  return () => { state ^= state << 13; state ^= state >>> 17; state ^= state << 5; return (state >>> 0) / 0x100000000; };
}
export function poolConfig(env = process.env) {
  const pool = env.COOP_POOL ?? 'mixed', split = env.COOP_SPLIT ?? 'train';
  if (!Object.hasOwn(MANIFEST.pools, pool) || !['train', 'eval'].includes(split)) throw new Error('Invalid COOP_POOL/COOP_SPLIT');
  const rawSeed = env.COOP_POOL_SEED ?? '1';
  if (!/^[1-9][0-9]*$/.test(rawSeed)) throw new Error('Invalid COOP_POOL_SEED');
  const poolSeed = uint32(Number(rawSeed), 'COOP_POOL_SEED');
  const members = MANIFEST.pools[pool];
  const fixed = env.COOP_TEAM === undefined ? null : env.COOP_TEAM.split(',');
  if (fixed && (fixed.length !== 3 || fixed.some(id => !members.includes(id)))) throw new Error('COOP_TEAM needs exactly three member IDs from COOP_POOL');
  if (split === 'train' && members.some(id => MANIFEST.members.find(m => m.id === id).split === 'heldout')) {
    throw new Error('Heldout families cannot enter a training pool');
  }
  return freeze({ pool, split, poolSeed, fixed });
}
export function selectTeam(config, seed) {
  uint32(seed, 'seed');
  const rng = localRng(config.poolSeed, `partners/${seed}`), ids = MANIFEST.pools[config.pool];
  return Array.from({ length: 3 }, (_, i) => MANIFEST.members.find(m => m.id ===
    (config.fixed ? config.fixed[i] : ids[Math.floor(rng() * ids.length)])));
}
