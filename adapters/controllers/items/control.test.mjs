import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { control, planItem, VERSION, LIMITS } from './control.mjs';
import { transform, load, VERSION as LOADER_VERSION } from './loader.mjs';
import { ShopEnv } from '../rl/env.mjs';
const data = JSON.parse(await readFile(new URL('../../Stronghold-Protocol/data/items.json', import.meta.url)));
const records = {
  diy: { tier: 5, isDiy: true, diyFor: null },
  tank: { baseId: 'tank', goldenId: 'elite', tier: 3, position: 'MELEE', attackKind: 'melee', bonds: ['a'], stats: { atk: 100, def: 800, blockCnt: 3, maxHp: 5000 } },
  dealer: { baseId: 'dealer', goldenId: 'elite', tier: 5, position: 'RANGED', attackKind: 'ranged', bonds: ['b'], stats: { atk: 1000, bat: 1 } },
  elite: { baseId: 'dealer', goldenId: 'elite', isGolden: true, tier: 5, position: 'RANGED', attackKind: 'ranged', bonds: ['b'], stats: { atk: 1500 } },
};
const gd = { item: id => data[id], chess: id => records[id], equipPerChess: 2, maxArtsPerRound: 3 };
const piece = (id, uid, items = []) => ({ kind: 'chess', id, uid, items, row: 10, col: uid + 2 });
const own = () => ({ board: [piece('tank', 1), piece('dealer', 2)], hand: Array(10).fill(null), temp: [], deployCap: 8,
  shop: { level: 5, slots: [{ kind: 'chess', id: 'dealer', sold: false }] }, bonds: [{ bondId: 'a', count: 1 }, { bondId: 'b', count: 3, active: true }] });
const item = (id, uid = 50) => ({ kind: 'item', id: `chess_item_${id}`, uid });

test('role targeting, bond signatures, full equipment protection for every consumable', () => {
  let o = own();
  assert.equal(planItem(o, item('1_01_e_a'), gd).args[1], 2);
  assert.equal(planItem(o, item('1_02_e_a'), gd).args[1], 1);
  const signatureGD = { ...gd, chess: id => ({ ...records[id], bonds: id === 'tank' ? ['lateranoShip'] : [] }) };
  assert.equal(planItem(o, item('6_02_e_a'), signatureGD).args[1], 1);
  for (const rec of Object.values(data).filter(r => r.kind.startsWith('consume_on_equip'))) {
    o = own(); o.board.forEach(p => p.items = [{ id: 'protect1' }, { id: 'protect2' }]);
    assert.equal(planItem(o, { id: rec.id, uid: 9 }, gd).method, undefined, rec.id);
  }
  o = own(); o.board[0].id = 'elite';
  assert.equal(planItem(o, item('5_06_e_b'), gd).args[1], 2);
  o.board[1].items = [item('5_06_e_a')];
  assert.equal(planItem(o, item('5_06_e_b'), gd).method, undefined);
  o = own(); o.hand[0] = piece('tank', 3);
  assert.equal(planItem(o, item('5_05_e_a'), gd).args[1], 1);
  assert.equal(planItem(o, item('1_04_e_a'), gd).args[1], 2);
});

test('Arts, capacity, unsupported and unknown items never silently discarded', () => {
  const o = own();
  assert.deepEqual(planItem(o, item('6_02_m'), gd).args, [50, 10, 4, 'RIGHT']);
  assert.equal(planItem(o, item('6_02_m'), gd, 3).reason, 'art-limit');
  assert.equal(planItem(o, item('6_03_m'), gd).reason, 'bounty-risk-retained');
  assert.equal(planItem(o, item('6_01_m'), gd).method, 'destroy');
  for (const id of ['5_04_e_a', '5_08_e_a', 'unknown']) assert.equal(planItem(o, item(id), gd).method, undefined);
  o.deployCap = 9;
  assert.equal(planItem(o, item('6_08_e_a'), gd).reason, 'deploy-cap-already-satisfied');
  o.shop.slots = [];
  assert.equal(planItem(o, item('2_02_e_a'), gd).reason, 'no-visible-shop-chess');
  o.hand.fill(piece('dealer', 77));
  assert.equal(planItem(o, item('6_02_m'), gd).method, undefined);
  assert.equal(planItem(o, item('2_06_e_a'), gd).reason, 'grant-needs-hand-room');
});

function fakeEnv({ reject = false, overflow = false } = {}) {
  const o = own(); o.hand[0] = item('1_03_e_a'); if (overflow) o.temp.push(item('unknown', 60));
  let calls = 0;
  const ps = { ready: false, alive: true, gd, privateView: () => structuredClone(o),
    equip() { calls++; return reject ? { error: 'FIXTURE_REJECT' } : { ok: true }; }, move: () => ({ ok: true }),
    buy() { throw Error('buy escaped'); }, sell() { throw Error('sell escaped'); }, refresh() { throw Error('refresh escaped'); }, levelUp() { throw Error('level escaped'); } };
  const bot = { arrange(m, p) { p.sell(1); p.buy(0); p.refresh(); p.levelUp(); } };
  // Throwing hidden getters prove the new item paths do not use them (arrange stub is deliberate).
  const m = { phase: 'PREP', round: 1, autoPickPersonalChoice() {} };
  for (const k of ['pool', 'wave', 'rng', 'lastResults']) Object.defineProperty(m, k, { get() { throw Error(`hidden ${k}`); } });
  return { env: { m, ps, bot, history: {}, source: 'policy', controllerBlockedSales: 0 }, o, calls: () => calls };
}

test('bounded attempts, failures, economy boundary, method/source restoration, explicit stop', () => {
  const f = fakeEnv({ reject: true }), original = f.env.ps.sell;
  for (let i = 0; i < 5; i++) control(f.env);
  f.env.m.round++;
  control(f.env);
  assert.equal(f.calls(), 1);
  assert.equal(f.env.ps.sell, original); assert.equal(f.env.source, 'policy');
  assert.equal(f.env.history.itemController.counts.failure, 1);
  assert(f.env.controllerBlockedSales > 0);
  const s = fakeEnv({ overflow: true });
  assert.throws(() => control(s.env, { forced: true }), /CONTROLLER_V2_STOP/);
  assert.equal(s.o.temp.length, 1); assert.equal(s.env.source, 'policy');
  assert(s.env.history.itemController.events.some(e => e.status === 'stop'));
  const b = fakeEnv(); b.o.hand = Array.from({ length: 100 }, (_, i) => item('1_03_e_a', 100 + i));
  control(b.env); assert.equal(b.calls(), LIMITS.itemsPerCall);
  control(b.env); assert.equal(b.calls(), LIMITS.itemsPerRound);
});

test('free rewards reject sold, paid, nonfinite, unknown and unslotted DIY', () => {
  const f = fakeEnv(); f.o.hand[0] = null;
  f.o.shop.rewardOffer = { slots: [null, { kind: 'chess', id: 'dealer', price: 1 },
    { kind: 'chess', id: 'dealer', price: 0, sold: true }, { kind: 'chess', id: 'dealer', price: NaN },
    { kind: 'item', id: 'unknown', price: 0 }, { kind: 'chess', id: 'diy', price: 0 }, { kind: 'chess', id: 'dealer', price: 0 }] };
  f.env.ps.pickReward = idx => { assert.equal(idx, 6); f.o.shop.rewardOffer = null; return { ok: true }; };
  control(f.env);
  assert(f.env.history.itemController.events.some(e => e.reason === 'free-visible-reward'));
});

test('loader only changes two anchors, rejects drift, off is exact result identity', async () => {
  const source = await readFile(new URL('../rl/env.mjs', import.meta.url), 'utf8');
  assert.equal(LOADER_VERSION, VERSION);
  const patched = transform(source);
  assert(patched.includes(`controller: '${VERSION}'`));
  assert.equal(patched.replace(/import \{ control \} from "file:[^"\n]+";/, "import { control } from './controller.mjs';")
    .replace(VERSION, 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1'), source);
  assert.throws(() => transform(source + '\n'), /SHA256 mismatch/);
  const result = { source: 'not even pinned', format: 'module' }, previous = process.env.ARK_ITEM_CONTROLLER;
  try {
    delete process.env.ARK_ITEM_CONTROLLER;
    assert.equal(await load(new URL('../rl/env.mjs', import.meta.url).href, {}, async () => result), result);
    process.env.ARK_ITEM_CONTROLLER = 'v2';
    assert.equal(await load(new URL('../rl/controller.mjs', import.meta.url).href, {}, async () => result), result);
    await assert.rejects(load(new URL('../rl/env.mjs', import.meta.url).href, {}, async () => result), /SHA256 mismatch/);
  } finally { if (previous === undefined) delete process.env.ARK_ITEM_CONTROLLER; else process.env.ARK_ITEM_CONTROLLER = previous; }
});

test('real-engine fixtures: gains, promotion, copy Art, destroy reward and locked equipment', async () => {
  // Artificial grants ONLY in this fixture. These are interface checks, never win-rate evidence.
  const e = await ShopEnv.create();
  try {
    e.reset({ seed: 7 });
    const ps = e.ps;
    ps.acquireChess('chess_char_1_01_a', { source: 'controller-v2-fixture' });
    control(e);
    e.bot = { arrange() {} };
    const carrier = [...ps.board.values()].find(p => p.kind === 'chess' && !ps.gd.chess(p.id).isGolden);
    assert(carrier);
    const grant = id => { const p = ps.acquireItem(`chess_item_${id}`, { source: 'controller-v2-fixture', displayed: true }); assert(p); return p; };
    const coin = grant('1_03_e_a'), funds = ps.funds;
    control(e); assert(!ps.find(coin.uid)); assert(ps.funds > funds);
    const old = ps.privateView().board.length + ps.privateView().hand.filter(p => p?.kind === 'chess').length;
    const recruit = grant('2_02_e_a'); control(e); assert(!ps.find(recruit.uid));
    assert(chess(ps.privateView()).length >= old); // grants may merge
    const scroll = grant('6_02_m'), arts = ps.round.arts;
    control(e); assert(!ps.find(scroll.uid)); assert.equal(ps.round.arts, arts + 1);
    const mystery = grant('6_01_m'), before = ps.funds;
    control(e); assert(!ps.find(mystery.uid)); assert(ps.funds > before); assert.equal(ps.personalChoice, null);
    const whip = grant('6_03_m'); control(e); assert(ps.find(whip.uid));
    const projection = grant('5_06_e_b'); control(e);
    assert(!ps.find(projection.uid));
    assert(chess(ps.privateView()).some(p => ps.gd.chess(p.id).isGolden));
    const log = e.history.itemController.events;
    assert(log.some(x => x.method === 'useArt' && x.status === 'success'));
    assert.equal(log.filter(x => x.status === 'failure').length, 0);
    assert.equal(e.engineErrors(), 0);
    // Fill every carrier through real equip calls, then prove a consumable cannot replace any item.
    for (const p of chess(ps.privateView())) {
      while ((ps.find(p.uid).piece.items || []).length < ps.gd.equipPerChess) {
        const it = grant('1_01_e_b');
        assert(ps.equip(it.uid, p.uid).ok);
      }
    }
    const protectedItems = chess(ps.privateView()).flatMap(p => p.items.map(i => i.uid));
    const blockedCoin = grant('1_03_e_a'), lockedFunds = ps.funds;
    control(e);
    assert(ps.find(blockedCoin.uid)); assert.equal(ps.funds, lockedFunds);
    for (const uid of protectedItems) assert.equal(ps.find(uid).area, 'equipped');
  } finally { e.close(); }
});
const chess = o => [...o.board, ...o.hand, ...o.temp].filter(p => p?.kind === 'chess');
