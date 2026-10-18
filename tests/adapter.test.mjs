import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { PlayerGame, RULES } from '../arknights/adapter/backend.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const backend = path.join(root, 'arknights/adapter/backend.mjs');
async function create(t, options = {}) {
  const game = await PlayerGame.create({ root, ...options });
  t.after(() => game.close());
  return game;
}
async function accepted(game, action) {
  const reply = await game.act(action);
  assert.equal(reply.accepted, true, JSON.stringify({ action, result: reply.result }));
  assert.equal(reply.result.ok, true);
  return reply;
}
async function prep(game, options = {}) {
  let state = await game.reset({ seed: 1, ...options });
  await accepted(game, { type: 'confirm' });
  for (let n = 0; n < 8; n++) {
    state = await game.wait();
    if (state.phase === 'BAND_DRAFT' && state.can_act) break;
  }
  assert.equal(state.phase, 'BAND_DRAFT');
  assert.equal(state.can_act, true);
  const bandId = state.actions.find(a => a.t === 'g.band').targets.bandId[0];
  await accepted(game, { type: 'band', bandId });
  for (let n = 0; n < 8; n++) {
    state = await game.wait();
    if (state.phase === 'PREP' && state.can_act) return state;
  }
  assert.fail('Did not reach the first native prep');
}
function keysDeep(value) {
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, child]) => [key, ...keysDeep(child)]);
}

test('human briefing, manual band focus/pick and wait at an actionable point', async t => {
  const game = await create(t);
  const initial = await game.reset({ seed: 1 });
  assert.equal(initial.apiVersion, 1);
  assert.equal(initial.rules, RULES);
  assert.equal(initial.season, 'act2autochess');
  assert.equal(initial.phase, 'INFO_CHECK');
  assert.equal(initial.public.players[0].isBot, false);
  assert.equal(initial.can_act, true);
  assert.equal(initial.terminal, false);
  assert.equal(initial.outcome, null);
  assert.deepEqual(initial, game.observe());
  assert.deepEqual(await game.wait(), initial, 'untimed wait cannot confirm for the learner');
  const reply = await accepted(game, { type: 'confirm', revision: initial.revision });
  assert.equal(reply.state.phase, 'INFO_CHECK', 'act never drains scheduled callbacks');
  const draft = await game.wait();
  assert.equal(draft.phase, 'BAND_DRAFT');
  const bandId = draft.actions.find(a => a.t === 'g.band').targets.bandId[0];
  const focused = await accepted(game, { type: 'bandFocus', bandId });
  assert.equal(focused.state.own.bandId, null);
  assert.deepEqual(await game.wait(), focused.state, 'focus does not auto-pick an untimed band');
  const picked = await accepted(game, { t: 'g.band', bandId });
  assert.equal(picked.state.own.bandId, bandId);
  assert.equal(picked.state.phase, 'BAND_DRAFT');
});

test('rejected, stale and forbidden report intents execute nothing', async t => {
  const game = await create(t);
  const before = await game.reset();
  let calls = 0;
  const handle = game.m.handle.bind(game.m);
  game.m.handle = (...args) => { calls++; return handle(...args); };
  for (const action of [null, {}, { type: 'buy', slot: -1 }, { t: 'b.result', result: {} },
    { t: 'b.progress' }, { type: 'buy', slot: 0, revision: before.revision + 1 }]) {
    const reply = await game.act(action);
    assert.equal(reply.accepted, false);
    assert.deepEqual(reply.state, before);
  }
  assert.equal(calls, 0);
  const wrongPhase = await game.act({ type: 'buy', slot: 0 });
  assert.equal(calls, 1, 'well-formed player intents reach actual Match.handle');
  assert.equal(wrongPhase.result.error, 'WRONG_PHASE');
  assert.deepEqual(wrongPhase.state, before);
});

test('real shop buy, manual move/equip, stats, freeze, refresh, sell and upgrade', async t => {
  const game = await create(t);
  let state = await prep(game, { seed: 3 });
  assert.equal(state.round, 1);
  assert(state.actions.find(a => a.t === 'g.buy').targets.slot.includes(0));
  const before = game.observe();
  assert.deepEqual(await game.wait(), before, 'waiting in prep never arranges or equips');
  let reply = await accepted(game, { type: 'buy', slot: 0 });
  const chess = reply.state.own.hand.find(p => p?.kind === 'chess');
  assert(chess);
  assert.equal(reply.state.own.board.length, 0, 'buy does not arrange');
  reply = await accepted(game, { type: 'buy', slot: 3 });
  const item = reply.state.own.hand.find(p => p?.kind === 'item');
  assert(item);
  assert.equal(reply.state.own.hand.find(p => p?.uid === chess.uid).items.length, 0, 'buy does not equip');
  reply = await accepted(game, { type: 'equip', itemUid: item.uid, targetUid: chess.uid });
  assert.equal(reply.state.own.hand.find(p => p?.uid === chess.uid).items[0].uid, item.uid);
  // Fixed-seed stage fixture: legal melee tile from the public stage's deployTiles, not action probing.
  const [row, col] = game.data.stages[state.public.stageId].deployTiles.normal.melee[0];
  reply = await accepted(game, { type: 'move', uid: chess.uid, to: { area: 'board', row, col }, dir: 'UP' });
  assert.equal(reply.state.own.board.find(p => p.uid === chess.uid).dir, 'UP');
  reply = await accepted(game, { type: 'unitStats', seq: 7 });
  assert(reply.result.events.some(e => e.t === 'm.unitStats' && e.seq === 7 && e.units.some(u => u.uid === chess.uid)));
  const frozen = await accepted(game, { type: 'freeze' });
  assert.equal(frozen.state.own.shop.frozen, true);
  await accepted(game, { type: 'refresh' });
  await accepted(game, { type: 'move', uid: chess.uid, to: { area: 'hand', idx: 0 } });
  reply = await accepted(game, { type: 'sell', uid: chess.uid });
  assert(!reply.state.own.hand.some(p => p?.uid === chess.uid));
  // One bounded native round unlocks an affordable upgrade without granting test funds.
  state = await prep(game);
  await accepted(game, { type: 'ready' });
  for (let n = 0; n < 32; n++) {
    state = await game.wait();
    if (state.phase === 'PREP' && state.round === 2) break;
  }
  assert.equal(state.round, 2);
  assert.equal(state.phase, 'PREP');
  reply = await accepted(game, { type: 'levelUp' });
  assert.equal(reply.state.own.shop.level, state.own.shop.level + 1);
});

test('combat is paced and wait preserves pause/watch controls and callback bounds', async t => {
  const game = await create(t);
  await prep(game);
  await accepted(game, { type: 'ready' });
  const combat = await game.wait();
  assert.equal(combat.phase, 'COMBAT');
  assert.equal(game.sched.instant, false);
  const paused = await accepted(game, { type: 'pause', on: true });
  assert.equal(paused.state.public.paused, true);
  assert.deepEqual(await game.wait(), paused.state);
  const watched = await accepted(game, { type: 'watch', fieldId: combat.public.fields[0].fieldId });
  assert(watched.result.events.some(e => e.t === 'm.field'));
  assert(watched.result.events.some(e => e.t === 'b.snap'));
  await accepted(game, { type: 'pause', on: false });
  const count = game.sched.executed;
  const progressed = await game.wait({ maxSteps: 2 });
  assert(game.sched.executed - count <= 2);
  assert(progressed.revision > combat.revision);
  await assert.rejects(game.wait({ maxSteps: 4097 }), /maxSteps/);
  assert.equal(game.m.errorCount, 0);
  assert.equal(game.sched.errors.length, 0);
});

test('online allowlist and recursive public-text lookup never expose hidden state/raw values', async t => {
  const game = await create(t);
  const state = await prep(game);
  assert.deepEqual(state.public.drawnDisabledBonds, game.m.publicView().drawnDisabledBonds);
  assert.deepEqual(state.public.disabledBonds, game.m.publicView().disabledBonds);
  assert(!keysDeep(state).some(k => /seed|rng|hiddenBossId/i.test(k)));
  for (const enemy of state.own.nextEnemies) {
    assert(Object.keys(enemy).every(k => ['enemyKey', 'count', 'gate', 't', 'fly', 'elite', 'boss', 'source', 'tag', 'start'].includes(k)));
  }
  for (const kind of ['chess', 'items', 'bonds', 'bands', 'garrisons', 'bosses']) {
    const result = game.lookup({ kind, limit: 100 });
    assert.equal(result.season, 'act2autochess');
    assert(result.source.endsWith(RULES));
    assert(result.entries.length > 0);
    assert(!keysDeep(result).some(k => /^(bb|bbStr|stats|params|bloodPoint|templates|spec|descRaw|weight|raw|seed|hiddenBossId)$/.test(k)));
    const numeric = value => typeof value === 'number' || value && typeof value === 'object' && Object.values(value).some(numeric);
    assert(!numeric(result.entries), 'lookup is text/identifiers only at every depth');
  }
  const first = game.lookup({ kind: 'bands', limit: 1 }).entries[0];
  const wideId = first.id.replace(/[a-z]/g, c => String.fromCharCode(c.charCodeAt(0) + 0xfee0));
  assert.deepEqual(game.lookup({ kind: 'bands', id: wideId }).entries, [first]);
  assert(game.lookup({ kind: 'bands', text: first.name }).entries.some(e => e.id === first.id));
  const hidden = Object.entries(game.data.chess).find(([, rec]) => rec.isHidden || rec.visible === false);
  assert(hidden);
  assert.deepEqual(game.lookup({ kind: 'chess', id: hidden[0] }).entries, []);
  assert.throws(() => game.lookup({ kind: 'enemies' }), /Unsupported/);
  assert.throws(() => game.lookup({ kind: 'chess', limit: 0 }), /limit/);
});

test('player-visible terrain and card memberships are available without action probes', async t => {
  const game = await create(t);
  const state = await prep(game);
  assert(state.own.deployment.cells.length > 0);
  assert.deepEqual(game.observe(), state, 'reading terrain is observational');
  const card = game.lookup({ kind: 'chess', id: 'chess_char_2_16_a' }).entries[0];
  assert(card.bonds.includes('siracusaShip'));
  assert(card.garrisonIds.length > 0);
  const trait = game.lookup({ kind: 'garrisons', id: card.garrisonIds[0] }).entries[0];
  assert(trait.desc);
  const old = state.own.deployment.cells[0];
  const key = `${old.row},${old.col}`;
  game.ps.tileOverrides[key] = old.kind === 'melee' ? 'ranged' : 'melee';
  game.ps.invalidateDeployMap();
  const updated = game.observe().own.deployment;
  assert.equal(updated.cells.find(c => c.row === old.row && c.col === old.col).kind, game.ps.tileOverrides[key]);
  assert.deepEqual(game.observe().own.deployment, updated);
  assert(updated.cells.every(c => ['melee', 'ranged'].includes(c.kind)));
});

test('room loadout/reroll route upstream, ownership and DIY are pre-start settings', async t => {
  const game = await create(t);
  let state = await game.reset({ ownership: ['chess_char_3_01_a'], diy: {} });
  assert(state.own.standIns.includes('chess_char_3_01_a'));
  await accepted(game, { type: 'loadout', entries: { chess_char_1_01_a: { skill: 0 } } });
  const reply = await accepted(game, { type: 'reroll', setupRevision: state.public.setupRevision });
  assert.equal(reply.state.public.setupRevision, state.public.setupRevision + 1);
  assert.equal(reply.state.phase, 'INFO_CHECK');
  assert.equal(reply.state.own.loadout.chess_char_1_01_a.skill, 0);
  state = reply.state;
  const stale = await game.act({ t: 'g.infoReady', setupRevision: 0 });
  assert.equal(stale.accepted, false);
  assert.deepEqual(stale.state, state);
  const locked = await game.act({ type: 'ownership', notOwned: [] });
  assert.equal(locked.result.error, 'ROOM_STARTED');
  assert.deepEqual(locked.state, state);
  await assert.rejects(game.reset({ loadout: { missing: { skill: 0 } } }), /BAD_TARGET/);
  assert.deepEqual(game.observe(), state, 'invalid reset leaves the active game intact');
  await assert.rejects(game.reset({ ops: { missing: { potential: 1 } } }), /BAD_TARGET/);
  await accepted(game, { type: 'confirm' });
  assert.equal((await game.wait()).phase, 'BAND_DRAFT');
});

test('coop native partner profiles preserve human-only online private observation', async t => {
  const game = await create(t);
  for (const partners of ['original', 'mixed', 'weak']) {
    const state = await prep(game, { mode: 'coop', partners, seed: 2 });
    assert.deepEqual(state.public.players.map(p => p.playerId), ['learner', 'partner1', 'partner2', 'partner3']);
    assert.deepEqual(state.public.players.map(p => p.isBot), [false, true, true, true]);
    // Observe must not request another seat's privateView, even when those seats are native bots.
    for (const ps of game.m.order.slice(1)) ps.privateView = () => { throw new Error('Other seat private view read'); };
    assert.deepEqual(game.observe(), state);
    // Restore normal native views before advancing bot prep.
    for (const ps of game.m.order.slice(1)) delete ps.privateView;
    await accepted(game, { type: 'ready' });
    const next = await game.wait();
    assert.equal(next.phase, 'COMBAT');
    assert.equal(game.m.errorCount, 0);
    assert.equal(game.sched.errors.length, 0);
    assert.equal(game.observe().own.playerId, 'learner');
  }
});

test('valid DIY reset initializes native private stock before the match starts', async t => {
  const game = await create(t);
  const slot = 'chess_char_5_diy1_a';
  const state = await game.reset({ seed: 11, diy: { [slot]: { charId: 'char_609_acguad' } } });
  assert.equal(state.phase, 'INFO_CHECK');
  assert.deepEqual(state.own.diy[slot], {
    charId: 'char_609_acguad', skillIndex: 2, uniEquipId: 'uniequip_002_acguad',
  });
  assert.deepEqual(state.own.diyBanned, []);
  const stock = game.ps.diyStock.entries.get(slot);
  assert(stock && stock.left > 0 && stock.cap > 0, 'valid DIY gets actual copies before any player action');
  assert.equal(game.ps.poolOf(slot), game.ps.diyStock);
  assert.equal(game.ps.gd.chess(slot).diyFor, slot);
});

test('native leave produces a sanitized full terminal settlement without simulating a full game', async t => {
  const game = await create(t);
  await game.reset();
  const reply = await accepted(game, { type: 'leave' });
  assert.equal(reply.state.terminal, true);
  assert.equal(reply.state.phase, 'RESULT');
  assert.equal(reply.state.outcome.team.reason, 'abandoned');
  assert.equal(reply.state.outcome.learner.playerId, 'learner');
  assert.equal(reply.state.outcome.summary.players.length, 1);
  assert(!keysDeep(reply.state).some(k => /seed|rng|hiddenBossId/i.test(k)));
});

test('JSONL envelopes recover after bad input and close cleanly', async () => {
  const requests = ['{bad', { cmd: 'reset', seed: 3 }, { cmd: 'observe' },
    { cmd: 'lookup', query: { kind: 'bands', limit: 1 } }, { cmd: 'act', action: { type: 'buy', slot: -1 } },
    { cmd: 'wait' }, { cmd: 'close' }];
  const child = spawn(process.execPath, [backend, '--root', root], { stdio: ['pipe', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.on('data', chunk => { stdout += chunk; });
  child.stderr.on('data', chunk => { stderr += chunk; });
  child.stdin.end(requests.map(r => typeof r === 'string' ? r : JSON.stringify(r)).join('\n') + '\n');
  const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
  assert.equal(code, 0, stderr);
  assert.equal(stderr, '');
  const replies = stdout.trim().split('\n').map(line => JSON.parse(line));
  assert.equal(replies.length, requests.length);
  assert.equal(replies[0].ok, false);
  assert.deepEqual(replies[1], replies[2]);
  assert.deepEqual(Object.keys(replies[3]), ['ok', 'result']);
  assert.deepEqual(Object.keys(replies[4]), ['ok', 'accepted', 'result', 'state']);
  assert.equal(replies[4].accepted, false);
  assert.deepEqual(replies[5], replies[2]);
  assert.deepEqual(replies[6], { ok: true });
});

test('recorder attachment leaves online replies identical and records rejections/wait', async t => {
  if (!existsSync(path.join(root, 'arknights/replay/recorder.mjs'))) return t.skip('Recorder worker has not landed');
  const online = await create(t), recorded = await create(t, { record: true, recordDir: '/virtual/bundle with punctuation.v1' });
  assert.deepEqual(await online.reset({ seed: 7 }), await recorded.reset({ seed: 7 }));
  assert.match(recorded.recorder.id, /^bundle_with_punctuation_v1-[A-Za-z0-9-]+$/);
  for (const action of [{ type: 'buy', slot: -1 }, { type: 'confirm' }]) {
    assert.deepEqual(await online.act(action), await recorded.act(action));
  }
  assert.deepEqual(await online.wait(), await recorded.wait());
  const bandId = online.observe().actions.find(a => a.t === 'g.band').targets.bandId[0];
  assert.deepEqual(await online.act({ type: 'band', bandId }), await recorded.act({ type: 'band', bandId }));
  assert(recorded.recorder.events.some(e => e.playerId === 'learner' && e.method === 'pickBand' && e.source === 'policy'));
  assert.deepEqual(await online.wait(), await recorded.wait());
  assert.deepEqual(await online.act({ type: 'ready' }), await recorded.act({ type: 'ready' }));
  assert.deepEqual(await online.wait(), await recorded.wait());
  assert.deepEqual(await online.wait({ maxSteps: 4 }), await recorded.wait({ maxSteps: 4 }));
  assert(recorded.recorder.commands.some(c => c.result.accepted === false));
  assert(recorded.recorder.commands.some(c => c.command.cmd === 'wait'));
});
