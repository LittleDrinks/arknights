#!/usr/bin/env node
// Player boundary for the pristine, pinned engine. No legacy policy/controller imports.
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import readline from 'node:readline';

export const RULES = '6ab2468279e926d107788f63d7fe219b85f67b4a';
const SEASON = 'act2autochess';
const LEARNER = 'learner';
const quiet = { info() {}, debug() {}, warn() {}, error() {} };
const copy = value => JSON.parse(JSON.stringify(value));
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.getPrototypeOf(value) === Object.prototype;
const pick = (value, keys) => Object.fromEntries(keys.filter(k => Object.hasOwn(value ?? {}, k)).map(k => [k, value[k]]));
const normalizeText = value => String(value).normalize('NFKC').toLowerCase();
const PUBLIC_KEYS = ['t', 'phase', 'round', 'lastRound', 'deadline', 'serverNow', 'modeId', 'difficulty',
  'stageId', 'setupRevision', 'rerollVote', 'factions', 'disabledBonds', 'drawnDisabledBonds', 'bannedChess',
  'bossId', 'bossRound', 'hiddenRound', 'bossPairs', 'spRound', 'combatMode', 'paused', 'players', 'fields',
  'teamLp', 'overtimeAt', 'bossHp', 'draft', 'sp', 'unite', 'uniteResult'];
const PREVIEW_KEYS = ['enemyKey', 'count', 'gate', 't', 'fly', 'elite', 'boss', 'source', 'tag', 'start'];
// Only published text and identifiers: never raw records, blackboards, params, formulas or stat tables.
const TEXT_KEYS = new Set(['name', 'appellation', 'desc', 'description', 'text', 'effectName', 'unlockDesc',
  'subProfessionName', 'profession', 'position', 'eventTypeDesc', 'skillType', 'durationType', 'spType']);
const ID_KEYS = new Set(['id', 'chessId', 'baseId', 'goldenId', 'charId', 'skillId', 'garrisonId', 'bandId', 'bossId',
  'effectId', 'uniEquipId', 'subProfessionId']);
const TEXT_CONTAINERS = new Set(['trait', 'skill', 'skills', 'talents', 'module', 'modules', 'abilities']);
const ALIASES = { confirm: 'g.infoReady', infoReady: 'g.infoReady', loadout: 'room.loadout',
  reroll: 'room.rerollSetup', rerollSetup: 'room.rerollSetup', cancelReroll: 'room.cancelReroll',
  ownership: 'room.ownership', diy: 'room.diy' };

function publicText(value, depth = 0) {
  if (depth > 8 || value == null) return null;
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(v => publicText(v, depth + 1)).filter(v => v != null);
  if (!plain(value)) return null;
  if (value.hidden === true || value.isHidden === true || value.visible === false) return null;
  const out = {};
  for (const [key, v] of Object.entries(value)) {
    if ((TEXT_KEYS.has(key) || ID_KEYS.has(key)) && typeof v === 'string') out[key] = v;
    else if (TEXT_CONTAINERS.has(key)) {
      const text = publicText(v, depth + 1);
      if (text != null) out[key] = text;
    }
  }
  return out;
}

// Defence in depth on the wire, including terminal summaries. The full public settlement remains otherwise intact.
function withoutSecrets(value) {
  if (Array.isArray(value)) return value.map(withoutSecrets);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([k]) => !/seed|rng|hiddenBossId/i.test(k))
    .map(([k, v]) => [k, withoutSecrets(v)]));
  return value;
}

function check(result) {
  if (!result?.ok) throw new Error(`${result?.error ?? 'BAD_MSG'}: ${result?.detail ?? 'invalid settings'}`);
  return result;
}

export class PlayerGame {
  static async create({ root, record = false, recordDir = null, frameEvery = 3, maxBytes } = {}) {
    root = path.resolve(root ?? fileURLToPath(new URL('../../', import.meta.url)));
    const gameRoot = existsSync(path.join(root, 'server/match/Match.js')) ? root : path.join(root, 'game');
    const git = args => execFileSync('git', ['-C', gameRoot, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
    if (git(['rev-parse', 'HEAD']) !== RULES) throw new Error('Rule revision mismatch');
    if (git(['status', '--porcelain', '--untracked-files=all'])) throw new Error('Game checkout must be pristine');
    const imp = relative => import(pathToFileURL(path.join(gameRoot, relative)).href);
    const [{ Match }, { VirtualScheduler }, { getData }, protocol, constants, bot, { Battle }] = await Promise.all([
      imp('server/match/Match.js'), imp('server/match/scheduler.js'), imp('server/data.js'),
      imp('shared/protocol.js'), imp('shared/constants.js'), imp('server/match/bot.js'), imp('server/sim/Battle.js'),
    ]);
    const data = getData({ log: quiet });
    if (!Object.keys(data.chess ?? {}).length) throw new Error('Game data unavailable');
    return new PlayerGame({ Match, VirtualScheduler, data, protocol, constants, bot, Battle,
      record, recordDir, frameEvery, maxBytes });
  }

  constructor(deps) {
    Object.assign(this, deps);
    this.revision = 0;
    this.m = null;
    this.recorder = null;
    this.finishedRecording = null;
    this.events = [];
  }

  async reset(options = {}) {
    if (!plain(options)) throw new Error('reset options must be an object');
    const { seed = 1, mode = 'solo', difficulty = 'ABYSS', partners = 'original' } = options;
    if (!Number.isInteger(seed) || seed < 1 || seed > 0xffffffff) throw new Error('seed must be uint32 in 1..4294967295');
    if (!['solo', 'coop'].includes(mode)) throw new Error('mode must be solo or coop');
    if (!this.constants.DIFFICULTIES.includes(difficulty)) throw new Error('Unsupported difficulty');
    if (!['original', 'mixed', 'weak'].includes(partners)) throw new Error('Unsupported partners');
    if (options.record !== undefined && typeof options.record !== 'boolean') throw new Error('record must be boolean');
    const { protocol: p, data } = this;
    const entries = options.loadout?.entries ?? options.loadout ?? {};
    const ops = options.ops ?? options.loadout?.ops;
    const notOwned = options.ownership?.notOwned ?? options.ownership ?? [];
    const diy = options.diy?.picks ?? options.diy ?? {};
    const wire = { t: 'room.loadout', entries, ...(ops === undefined ? {} : { ops }) };
    for (const msg of [wire, { t: 'room.ownership', notOwned }, { t: 'room.diy', picks: diy }]) {
      const error = p.validateC2S(msg);
      if (error) throw new Error(`BAD_MSG: ${error}`);
    }
    // Upstream strict loadout validation; ownership and DIY intentionally use upstream lenient semantics.
    check(p.checkLoadout(entries, id => data.chess[id] ?? null));
    const ids = p.cultivationCharIds(data.chess, data.backups);
    check(p.checkLoadoutOps(ops, id => ids.has(id), id => p.charPotentialChanges(data, id)));
    await this.close();
    this.summary = null;
    this.revision++;
    this.events = [];
    this.finishedRecording = null;
    this.recorder = null;
    this.sched = new this.VirtualScheduler({ start: 0, instantCombat: false, onError() {} });
    this.m = new this.Match({ roomCode: 'PLAYER', seed, mode, difficulty, data, scheduler: this.sched,
      clientCombat: false, botRehearsal: 0, botRehearsalDelegate: false,
      seats: Array.from({ length: mode === 'solo' ? 1 : 4 }, (_, seat) => ({ seat,
        playerId: seat ? `partner${seat}` : LEARNER, name: seat ? `Partner ${seat}` : 'Learner',
        isBot: seat !== 0, connected: true,
        // PlayerState invokes native setters before Match initializes per-seat DIY stock.
        ...(seat === 0 ? { loadout: entries, ops, notOwned, diy } : {}) })),
      send: (id, msg) => { if (id === LEARNER) this.capture(msg); return true; }, broadcast() {},
      onEnd: summary => { this.summary = withoutSecrets(copy(summary)); }, log: quiet,
    });
    this.ps = this.m.players.get(LEARNER);
    try {
      this.installPartners(partners);
      if (options.record ?? this.record) {
        const { Recorder } = await import('../replay/recorder.mjs');
        // Artifact identity never enters a Match option or an online observation.
        const basename = this.recordDir ? path.basename(path.resolve(this.recordDir)) : 'player';
        const prefix = basename.replace(/[^A-Za-z0-9_-]/g, '_').replace(/^[^A-Za-z0-9]+/, '').slice(0, 80) || 'player';
        this.recorder = new Recorder({ id: `${prefix}-${randomUUID()}`, recordDir: this.recordDir,
          frameEvery: this.frameEvery, ...(this.maxBytes === undefined ? {} : { maxBytes: this.maxBytes }) });
        await this.recorder.attach(this.m, this.Battle);
      }
      this.m.start();
      this.checkEngine();
      return this.observe();
    } catch (error) {
      this.m.dispose();
      this.sched.dispose();
      this.m = null;
      throw error;
    }
  }

  installPartners(profile) {
    if (profile === 'original') return;
    const m = this.m;
    const original = m.scheduleBotPrep.bind(m);
    m.scheduleBotPrep = (ps, i = 0) => {
      // Mixed: one original teammate, one two-buy teammate, one level-first saver.
      // Weak: two-buy / saver / two-buy. Bot policies never operate the human seat.
      if (!ps.isBot || (profile === 'mixed' && ps.seat === 1)) return original(ps, i);
      const round = m.round;
      m.later(m.scaled(90 + i * 30), () => {
        if (m.phase !== 'PREP' || m.round !== round || !ps.alive || ps.ready) return;
        m.autoPickPersonalChoice(ps, 'bot');
        const saver = ps.seat === 2;
        let own = ps.privateView();
        if (saver && own.shop.level < own.shop.maxLevel && own.funds >= own.shop.upgradePrice) ps.levelUp();
        for (let n = 0; n < (saver ? 1 : 2); n++) {
          own = ps.privateView();
          if (!own.hand.includes(null)) break;
          const slots = own.shop.slots.map((s, slot) => ({ ...s, slot })).filter(s => s.kind === 'chess' && !s.sold
            && s.price <= own.funds && !ps.gd.chess(s.id)?.isDiy);
          if (saver) slots.sort((a, b) => a.price - b.price || a.slot - b.slot);
          if (!slots.length) break;
          const result = ps.buy(slots[0].slot);
          if (!result?.ok) throw new Error(`Partner buy rejected: ${result?.error}`);
        }
        this.bot.arrange(m, ps);
        m.autoPickPersonalChoice(ps, 'bot');
        ps.resolveTemp();
        check(ps.setReady(true));
      });
    };
  }

  capture(msg) {
    // Only explicit player-visible replies are returned. b.start/specs and reports never cross this boundary.
    if (msg.t === 'm.unitStats') this.events.push(withoutSecrets(copy(msg)));
    else if (msg.t === 'b.snap') this.events.push(withoutSecrets(copy(pick(msg, [
      't', 'fieldId', 'gt', 'units', 'dp', 'dps', 'killed', 'total', 'resolved', 'boss', 'down',
      'elem', 'ammo', 'wolves', 'neg', 'energy', 'stand', 'standCut', 'coins', 'layers', 'charges',
    ]))));
    else if (msg.t === 'm.field') {
      const field = pick(msg, ['t', 'fieldId', 'kind', 'rect', 'stageId', 'units', 'effects', 'prep', 'side', 'mate', 'live']);
      if (msg.nextEnemies) field.nextEnemies = msg.nextEnemies.map(v => pick(v, PREVIEW_KEYS));
      this.events.push(withoutSecrets(copy(field)));
    }
    if (this.events.length > 16) this.events.shift();
  }

  checkEngine() {
    if (this.m?.errorCount || this.sched?.errors.length) throw new Error('Engine callback failed');
  }

  requireMatch() {
    if (!this.m || this.m.disposed) throw new Error('Reset required');
  }

  observe() {
    this.requireMatch();
    const own = copy(this.ps.privateView());
    own.nextEnemies = own.nextEnemies.map(v => pick(v, PREVIEW_KEYS));
    const pub = pick(this.m.publicView(), PUBLIC_KEYS);
    const terminal = !!this.m.ended;
    const actions = terminal ? [] : this.descriptors(own, pub);
    return withoutSecrets(copy({ apiVersion: 1, rules: RULES, season: SEASON, revision: this.revision,
      phase: pub.phase, round: pub.round, terminal, can_act: this.decision(own, pub), own, public: pub, actions,
      outcome: terminal && this.summary ? {
        team: pick(this.summary, ['victory', 'roundsPassed', 'hiddenReached', 'hiddenCleared', 'reason', 'teamLp']),
        learner: this.summary.players?.find(player => player.playerId === LEARNER) ?? null,
        summary: this.summary,
      } : null,
      clock: { mode: 'virtual', now: this.sched.now() },
      capabilities: [...Object.keys(this.protocol.C2S).filter(t => t.startsWith('g.')),
        'room.loadout', 'room.rerollSetup', 'room.cancelReroll', 'lookup', 'recording'],
    }));
  }

  // can_act means a substantive human decision, not merely an available emote/watch control.
  decision(own, pub) {
    if (this.m.ended || this.ps.left || !own.alive || this.ps.autoplay) return false;
    if (own.personalChoice) return true;
    if (pub.phase === 'INFO_CHECK') return !pub.players.find(p => p.playerId === LEARNER)?.ready;
    if (pub.phase === 'BAND_DRAFT') return pub.draft?.turn === LEARNER && !pub.draft.picks[LEARNER];
    if (pub.phase === 'SP_DRAFT') return pub.sp?.turn === LEARNER && pub.sp.picks[LEARNER] == null;
    return pub.phase === 'PREP' && !own.ready;
  }

  descriptors(own, pub) {
    const out = [];
    const add = (t, params = {}, targets = {}, constraints = 'Upstream validates phase, resources and target combinations') => {
      out.push({ type: t.replace(/^g\./, ''), t, params, targets, constraints });
    };
    if (this.ps.left) return out;
    add('g.leave');
    add('g.emote', { id: 'enum' }, { id: this.constants.EMOTES });
    add('g.autoplay', { on: 'boolean' });
    const fieldIds = pub.fields.length ? pub.fields.filter(f => !['boss', 'hidden'].includes(f.kind)
      || !own.alive || f.players.includes(LEARNER)).map(f => f.fieldId)
      : pub.players.filter(p => p.alive).map(p => `n:${p.playerId}`);
    add('g.watch', { fieldId: 'enum', playerId: 'optional player id' }, { fieldId: fieldIds });
    if (pub.modeId.startsWith('mode_single_')) add('g.pause', { on: 'boolean' }, {}, 'on:true requires a live solo combat; on:false resumes');
    if (pub.phase === 'INFO_CHECK') {
      add('g.infoReady', { setupRevision: 'integer' }, { setupRevision: [pub.setupRevision] });
      add('room.loadout', { entries: 'operator skill/module map', ops: 'optional potential/cultivate map' });
      add('room.rerollSetup', { setupRevision: 'integer' }, { setupRevision: [pub.setupRevision] });
      if (pub.rerollVote) {
        add('g.rerollVote', { voteId: 'integer', agree: 'boolean' }, { voteId: [pub.rerollVote.id] });
        add('room.cancelReroll', { voteId: 'integer' }, { voteId: [pub.rerollVote.id] });
      }
    }
    if (pub.phase === 'BAND_DRAFT' && !pub.draft?.picks[LEARNER]) {
      const ids = this.m.gd.bandIds();
      add('g.bandFocus', { bandId: 'optional id or null' }, { bandId: ids });
      if (pub.draft?.turn === LEARNER) {
        add('g.band', { bandId: 'enum' }, { bandId: ids.filter(id => !Object.values(pub.draft.picks).includes(id)) });
        if (!pub.modeId.startsWith('mode_single_') && pub.draft.skipsLeft[LEARNER] > 0) add('g.bandSkip');
      }
    }
    if (own.alive && ['ROUND_START', 'SP_DRAFT', 'PREP'].includes(pub.phase)) add('g.unitStats', { seq: 'optional integer' });
    if (own.personalChoice) add('g.choice', { idx: 'enum', choiceId: 'id' }, {
      idx: own.personalChoice.cards.map((_, i) => i), choiceId: [own.personalChoice.id] });
    else if (pub.phase === 'SP_DRAFT' && pub.sp?.turn === LEARNER) add('g.choice', { idx: 'enum' }, {
      idx: pub.sp.cards.map((c, i) => !c.soldOut && !pub.sp.taken[i] ? i : null).filter(i => i !== null) });
    if (!own.alive || pub.phase !== 'PREP') return out;
    add('g.ready', { ready: 'boolean' }, { ready: own.canReady ? [false, true] : [false] });
    if (own.ready) return out;
    const pieces = [...own.board, ...own.hand, ...own.temp].filter(Boolean);
    const chess = pieces.filter(p => p.kind === 'chess');
    const items = pieces.filter(p => p.kind === 'item');
    const allItems = [...items, ...chess.flatMap(p => p.items)];
    if (own.hand.includes(null)) add('g.buy', { slot: 'enum' }, { slot: own.shop.slots
      .map((s, slot) => s && !s.sold && s.price <= own.funds ? slot : null).filter(i => i !== null) });
    add('g.freeze');
    if (own.funds >= own.shop.refreshPrice) add('g.refresh');
    if (own.shop.level < own.shop.maxLevel && own.funds >= own.shop.upgradePrice) add('g.levelUp');
    if (chess.length) add('g.sell', { uid: 'enum' }, { uid: chess.map(p => p.uid) });
    if (pieces.length) add('g.move', { uid: 'enum', to: '{area:board,row,col} or {area:hand,idx}', dir: 'optional enum' }, {
      uid: pieces.map(p => p.uid), dir: this.protocol.DIRS, hand: own.hand.map((_, idx) => idx),
      board: { rows: [this.constants.GEO.FIELD.r0, this.constants.GEO.FIELD.r1],
        cols: [this.constants.GEO.FIELD.c0, this.constants.GEO.FIELD.c1] },
    }, 'Parameter domains only: upstream checks terrain, deploy cap, tokens, occupancy and swaps');
    if (allItems.length && chess.length) add('g.equip', { itemUid: 'enum', targetUid: 'enum', replaceUid: 'optional uid or null' }, {
      itemUid: allItems.map(p => p.uid), targetUid: chess.map(p => p.uid), replaceUid: chess.flatMap(p => p.items.map(i => i.uid)),
    });
    if (items.length) {
      add('g.art', { itemUid: 'enum', row: 'integer', col: 'integer', dir: 'optional enum' }, {
        itemUid: items.map(p => p.uid), dir: this.protocol.DIRS,
      }, 'Upstream checks Art type, target tile and direction');
      add('g.destroy', { uid: 'enum' }, { uid: items.map(p => p.uid) });
    }
    if (own.shop.rewardOffer) add('g.reward', { idx: 'enum' }, { idx: own.shop.rewardOffer.slots
      .map((s, idx) => !s.sold ? idx : null).filter(i => i !== null) });
    return out;
  }

  normalize(action) {
    if (!plain(action)) return { error: 'BAD_MSG', detail: 'action must be an object' };
    if (action.revision !== undefined && action.revision !== this.revision) return { error: 'STALE_REVISION' };
    const raw = action.t ?? action.type;
    if (typeof raw !== 'string') return { error: 'BAD_MSG', detail: 'action requires type or t' };
    const t = ALIASES[raw] ?? (raw.includes('.') ? raw : `g.${raw}`);
    if (!t.startsWith('g.') && !['room.loadout', 'room.rerollSetup', 'room.cancelReroll', 'room.ownership', 'room.diy'].includes(t)) {
      return { error: 'BAD_MSG', detail: 'Only player intents are accepted' };
    }
    const msg = { ...action, t };
    delete msg.type;
    delete msg.revision;
    if (t === 'g.infoReady' || t === 'room.rerollSetup') msg.setupRevision ??= this.m.setupRevision;
    if (t === 'g.ready') msg.ready ??= true;
    if (t === 'g.choice' && this.ps.privateView().personalChoice) msg.choiceId ??= this.ps.privateView().personalChoice.id;
    const error = this.protocol.validateC2S(msg);
    return error ? { error: 'BAD_MSG', detail: error } : { msg };
  }

  async act(action) {
    this.requireMatch();
    const before = this.observe();
    this.events = [];
    if (this.recorder) this.recorder.source = 'policy';
    let result;
    try {
      const normalized = this.normalize(action);
      if (normalized.error) result = normalized;
      else {
        const msg = normalized.msg;
        if (msg.t === 'room.loadout') {
          check(this.protocol.checkLoadout(msg.entries, id => this.data.chess[id] ?? null));
          const ids = this.protocol.cultivationCharIds(this.data.chess, this.data.backups);
          check(this.protocol.checkLoadoutOps(msg.ops, id => ids.has(id), id => this.protocol.charPotentialChanges(this.data, id)));
          result = this.m.setLoadout(LEARNER, msg.entries, msg.ops);
        } else if (msg.t === 'room.rerollSetup') result = this.m.requestSetupReroll(LEARNER, msg.setupRevision);
        else if (msg.t === 'room.cancelReroll') result = this.m.cancelSetupReroll(LEARNER, msg.voteId);
        else if (msg.t === 'room.ownership' || msg.t === 'room.diy') {
          result = { error: 'ROOM_STARTED', detail: 'ownership and diy are reset-only seat settings' };
        } else result = this.m.handle(LEARNER, msg);
        this.checkEngine();
      }
    } catch (error) {
      result = { error: 'ADAPTER_ERROR', detail: error.message };
    }
    const accepted = result?.ok === true;
    if (accepted) this.revision++;
    if (this.events.length) result = { ...result, events: copy(this.events) };
    const state = this.observe();
    await this.recorder?.recordCommand({ cmd: 'act', action: copy(action ?? null) }, before, state, { accepted, result });
    return { accepted, result, state };
  }

  async wait({ maxSteps = 512 } = {}) {
    this.requireMatch();
    const before = this.observe();
    const executed = this.sched.executed;
    if (this.recorder) this.recorder.source = 'engine';
    let failure = null;
    try {
      if (!Number.isInteger(maxSteps) || maxSteps < 1 || maxSteps > 4096) throw new Error('maxSteps must be in 1..4096');
      // Waiting at a live decision is a no-op unless there is an explicit native deadline.
      // Never run a queued bot callback past a human draft/choice or invent a timeout in untimed solo play.
      const phase = before.phase, round = before.round;
      const combat = ['COMBAT', 'UNITE', 'FINAL_ASSAULT', 'HIDDEN_CORE'].includes(phase);
      const decision = before.can_act;
      const deadline = decision && before.public.deadline > this.sched.now() ? before.public.deadline : null;
      if (!before.terminal && !before.public.paused && (!decision || deadline !== null)) {
        for (let n = 0; n < maxSteps; n++) {
          if (this.sched.nextAt() === null || !this.sched.runNext()) break;
          const view = this.observe();
          if (view.terminal || view.can_act && (!decision || view.phase !== phase || view.round !== round)) break;
          if (combat && (view.phase !== phase || view.round !== round)) break;
          if (!combat && ['COMBAT', 'UNITE', 'FINAL_ASSAULT', 'HIDDEN_CORE'].includes(view.phase)) break;
          if (deadline !== null && this.sched.now() >= deadline) break;
          // Small chunks keep combat controls responsive even during long fights.
          if (combat && n >= 31) break;
        }
      }
      this.checkEngine();
    } catch (error) { failure = error; }
    if (this.sched.executed !== executed) this.revision++;
    const state = this.observe();
    await this.recorder?.recordCommand({ cmd: 'wait', maxSteps }, before, state,
      failure ? { ok: false, error: failure.message } : { ok: true });
    if (failure) throw failure;
    return state;
  }

  lookup(query = {}) {
    if (!plain(query) || !['chess', 'items', 'bonds', 'bands', 'garrisons', 'bosses'].includes(query.kind)) throw new Error('Unsupported lookup kind');
    const { kind, id, text = '', limit = 20 } = query;
    if (id !== undefined && typeof id !== 'string' || typeof text !== 'string') throw new Error('lookup id/text must be strings');
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('lookup limit must be in 1..100');
    const wanted = id === undefined ? null : normalizeText(id);
    const needle = normalizeText(text);
    const result = [];
    for (const [key, rec] of Object.entries(this.data[kind] ?? {}).sort(([a], [b]) => a.localeCompare(b, 'en'))) {
      if (wanted !== null && normalizeText(key) !== wanted) continue;
      const safe = publicText(rec);
      if (!safe) continue;
      const entry = { id: key, ...safe };
      if (needle && !normalizeText(JSON.stringify(entry)).includes(needle)) continue;
      result.push(entry);
      if (result.length >= limit) break;
    }
    return { kind, season: SEASON, source: `game/data/${kind}.json@${RULES}`, entries: result };
  }

  async finishRecording() {
    this.requireMatch();
    if (!this.recorder) throw new Error('Recording is disabled');
    this.finishedRecording ??= Promise.resolve(this.recorder.finish(this.observe()));
    return await this.finishedRecording;
  }

  async close() {
    this.m?.dispose();
    this.sched?.dispose();
    this.m = null;
  }
}

async function main() {
  const args = process.argv.slice(2);
  const option = name => { const pos = args.indexOf(name); return pos < 0 ? undefined : args[pos + 1]; };
  const game = await PlayerGame.create({ root: option('--root'), recordDir: option('--record-dir') ?? null });
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  try {
    for await (const line of lines) {
      let closing = false;
      try {
        const request = JSON.parse(line);
        if (!plain(request)) throw new Error('request must be an object');
        let response;
        switch (request.cmd) {
          case 'reset': response = { state: await game.reset(request) }; break;
          case 'observe': response = { state: game.observe() }; break;
          case 'act': response = await game.act(request.action); break;
          case 'wait': response = { state: await game.wait(request) }; break;
          case 'lookup': response = { result: game.lookup(request.query) }; break;
          case 'recording': response = { result: await game.finishRecording() }; break;
          case 'close': await game.close(); response = {}; closing = true; break;
          default: throw new Error('Expected reset/observe/act/wait/lookup/recording/close');
        }
        process.stdout.write(`${JSON.stringify({ ok: true, ...response })}\n`);
      } catch (error) {
        process.stdout.write(`${JSON.stringify({ ok: false, error: error.message })}\n`);
      }
      if (closing) break;
    }
  } finally { await game.close(); lines.close(); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { process.stdout.write(`${JSON.stringify({ ok: false, error: error.message })}\n`); process.exitCode = 1; });
}
