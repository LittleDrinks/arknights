import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformTeacherBot, load } from './loader.mjs';
import { options, IDS, priority, buyVisibleSteps, reports, safeSell, lineupBonus } from './policy.mjs';
const source = readFileSync(new URL('../../Stronghold-Protocol/server/match/bot.js', import.meta.url), 'utf8');
test('fixed source composes once; source drift and a second transform fail closed', () => {
  const transformed = transformTeacherBot(source);
  assert.match(transformed, /__teacher.macroSteps/);
  assert.doesNotMatch(transformed, /ps\.sell\(/);
  assert.throws(() => transformTeacherBot(source + '\n'), /SHA256/);
  assert.throws(() => transformTeacherBot(transformed), /SHA256/);
});
test('off returns untouched loader result, including original source identity', async () => {
  delete process.env.ARK_TEACHER;
  const result = { format: 'module', source: Buffer.from(source) };
  assert.equal(await load(new URL('../../Stronghold-Protocol/server/match/bot.js', import.meta.url).href, {}, async () => result), result);
  assert.equal(options({}).on, false);
  assert.throws(() => options({ ARK_TEACHER: 'bogus' }));
  assert.throws(() => options({ ARK_TEACHER_BUDGET: 'bogus' }));
});
test('visible-only purchases obey shared gross budget, reserve, ready and failed-action gates', () => {
  // Contract fixture ONLY, not natural gameplay evidence.
  process.env.ARK_TEACHER = 'acquire';
  process.env.ARK_TEACHER_BUDGET = 'conservative';
  const m = { round: 1, phase: 'PREP' };
  Object.defineProperty(m, 'rngShop', { get() { throw Error('private RNG read'); } });
  const ps = { funds: 12, alive: true, ready: false, board: new Map(), hand: Array(8).fill(null), temp: [],
    gd: { baseIdOf: id => id, isGolden: () => false, modeInactiveBonds: new Set(),
      chess: id => ({ bonds: id === IDS.xiaoman ? ['yanShip'] : ['yanShip', 'arcaneShip'] }), bond: () => ({ thresholds: [2] }) },
    shop: { slots: [{ kind: 'chess', id: IDS.xiaoman, price: 3 }, { kind: 'chess', id: IDS.dusk, price: 5 }] },
    priceOf: slot => slot.price,
    buy(i) { const slot = this.shop.slots[i]; this.funds -= slot.price; slot.sold = true;
      this.hand[this.hand.indexOf(null)] = { kind: 'chess', id: slot.id, uid: i + 1, items: [] }; return { ok: true }; },
  };
  [...buyVisibleSteps(m, ps)];
  assert.equal(ps.funds, 4);
  assert.equal(reports(ps).setupCost, 8);
  ps.shop.slots = [{ kind: 'chess', id: IDS.xiaoman, price: 0 }];
  [...buyVisibleSteps(m, ps)];
  assert.equal(reports(ps).setupBuys, 3);
  [...buyVisibleSteps(m, ps)];
  assert.equal(reports(ps).buys.length, 3);
  m.round++;
  ps.ready = true;
  [...buyVisibleSteps(m, ps)];
  assert.equal(reports(ps).round, 1);
  ps.ready = false;
  ps.shop.slots[0].sold = false;
  ps.buy = () => ({ error: 'refused' });
  [...buyVisibleSteps(m, ps)];
  [...buyVisibleSteps(m, ps)];
  assert.equal(reports(ps).buys.length, 1);
  assert.equal(reports(ps).failed, true);
  ps.find = uid => ({ piece: ps.hand.find(p => p?.uid === uid) });
  ps.sell = () => { throw Error('protected core sold'); };
  assert.equal(safeSell(ps, 1).error, 'teacher-protected');
  assert.ok(priority(ps, IDS.dusk) > 0);
  assert.ok(lineupBonus(ps, ps.hand.filter(Boolean), { yanShip: { count: 2, active: true } }) > 0);
  delete process.env.ARK_TEACHER;
  delete process.env.ARK_TEACHER_BUDGET;
});
