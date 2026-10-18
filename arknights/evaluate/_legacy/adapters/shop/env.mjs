#!/usr/bin/env node
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import readline from 'node:readline';
import { encode, makeSchema, pieces } from './features.mjs';
import { control } from './controller.mjs';
export const RULES = '6ab2468279e926d107788f63d7fe219b85f67b4a';
const copy = x => JSON.parse(JSON.stringify(x));
export class ShopEnv {
  static async create(game = process.env.STRONGHOLD_GAME || fileURLToPath(new URL('../../Stronghold-Protocol', import.meta.url))) {
    game = path.resolve(game);
    const actual = execFileSync('git', ['-C', game, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    if (actual !== RULES) throw new Error(`Rule revision mismatch: ${actual}`);
    const dirty = execFileSync('git', ['-C', game, 'status', '--porcelain', '--untracked-files=no', '--', 'server', 'shared', 'data'], { encoding: 'utf8' });
    if (dirty.trim()) throw new Error('Tracked game rules are modified');
    const imp = f => import(pathToFileURL(path.join(game, f)));
    const [{ Match }, { VirtualScheduler }, { getData }, bot] = await Promise.all([
      imp('server/match/Match.js'), imp('server/match/scheduler.js'), imp('server/data.js'), imp('server/match/bot.js')]);
    const data = getData({ log: { info() {}, warn() {}, error() {} } });
    return new ShopEnv({ Match, VirtualScheduler, data, bot });
  }
  constructor(deps) { Object.assign(this, deps); this.schema = makeSchema(this.data); }
  close() { this.m?.dispose(); this.sched?.dispose(); }
  reset({ seed = 1, mode = 'solo', difficulty = 'NORMAL', budget = 128, teacher = false } = {}) {
    if (mode !== 'solo') throw new Error('v1 supports solo only');
    if (!['NORMAL', 'HARD', 'ABYSS'].includes(difficulty)) throw new Error('Unsupported difficulty');
    if (!Number.isInteger(seed) || seed < 1 || seed > 0xffffffff || !Number.isInteger(budget) || budget < 1 || budget > 1024) throw new Error('Invalid seed/budget: seed must be uint32 in 1..4294967295');
    this.close();
    Object.assign(this, { summary: null, actionErrors: 0, controllerErrors: 0, controllerBlockedSales: 0,
      budgetForcedReady: 0, engineLogErrors: [], steps: 0, prepRound: 0, lastBondState: null, teacher, source: 'fixed-controller', demos: [], demoRejected: 0 });
    this.episode = { seed, mode, difficulty, budget };
    this.history = { actions: [], bonds: [], pieces: {}, totals: {}, rounds: {}, byId: {}, prepSteps: 0, prepBudget: budget };
    this.sched = new this.VirtualScheduler({ onError: () => {} });
    this.m = new this.Match({ roomCode: 'RL', mode, difficulty, seed, data: this.data, scheduler: this.sched,
      botRehearsal: 0, botRehearsalDelegate: false, clientCombat: false,
      seats: [{ seat: 0, playerId: 'learner', name: 'Learner', isBot: true, connected: true }],
      send: () => true, broadcast() {}, onEnd: s => { this.summary = copy(s); },
      log: { info() {}, debug() {}, warn() {}, error: (...x) => this.engineLogErrors.push(x.map(String).join(' ')) } });
    this.ps = this.m.players.get('learner');
    // Keep original draft/choice automation, but replace ONLY the learner's prep scheduling.
    if (!teacher) this.m.scheduleBotPrep = () => {};
    this.installRecorder();
    this.m.start();
    if (teacher) {
      if (!this.sched.runUntil(() => !!this.summary, { maxSteps: 1000000 })) throw new Error('Teacher did not terminate');
    } else this.advance();
    return this.view();
  }
  observe() {
    const own = this.ps.privateView(), pub = this.m.publicView();
    if (this.prepRound !== pub.round) {
      this.prepRound = pub.round; this.history.prepSteps = 0;
      for (const p of Object.values(this.history.pieces)) { p.refreshes = 0; p.boardRefreshes = 0; }
    }
    // Public timestamps are irrelevant to policy; do not make seed/time proxies features.
    delete pub.serverNow; delete pub.deadline;
    for (const p of pieces(own)) {
      this.history.pieces[p.uid] ||= { id: p.id, firstRound: pub.round, refreshes: 0, boardRefreshes: 0 };
    }
    const bondState = JSON.stringify([own.bonds, own.effects]);
    if (bondState !== this.lastBondState) {
      this.history.bonds.push({ round: pub.round, action: this.history.actions.length, bonds: copy(own.bonds), effects: copy(own.effects) });
      this.lastBondState = bondState;
    }
    return { own, public: pub, history: this.history };
  }
  candidates(obs = this.observe()) {
    const p = obs.own;
    if (this.summary || this.m.phase !== 'PREP' || !p.alive || p.ready) return [];
    const out = [];
    if (p.canReady) out.push({ type: 'ready' });
    if (p.shop.freeRefreshes > 0 || p.funds >= p.shop.refreshPrice) out.push({ type: 'refresh', price: p.shop.refreshPrice });
    if (p.shop.level < p.shop.maxLevel && p.funds >= Math.max(0, p.shop.upgradePrice)) out.push({ type: 'levelUp', price: Math.max(0, p.shop.upgradePrice) });
    if (p.hand.includes(null)) p.shop.slots.forEach((s, slot) => {
      if (!s || s.sold || s.price > p.funds) return;
      const rec = s.kind === 'chess' ? this.ps.gd.chess(s.id) : this.ps.gd.item(s.id);
      if (!rec || (s.kind === 'chess' && rec.isDiy && !rec.diyFor)) return;
      out.push({ type: 'buy', slot, id: s.id, price: s.price, tier: rec.tier, golden: !!rec.isGolden, bonds: rec.bonds || [] });
    });
    for (const p of pieces(obs.own)) if (p.kind === 'chess') {
      const rec = this.ps.gd.chess(p.id);
      out.push({ type: 'sell', uid: p.uid, id: p.id, price: this.ps.gd.sellPrice(p.id), tier: rec?.tier, golden: p.golden, bonds: rec?.bonds || [] });
    }
    return out;
  }
  installRecorder() {
    const ps = this.ps;
    const methods = { buy: slot => ({ type: 'buy', slot }), sell: uid => ({ type: 'sell', uid }),
      refresh: () => ({ type: 'refresh' }), levelUp: () => ({ type: 'levelUp' }), setReady: ready => ({ type: 'ready', ready }),
      move: (uid, to, dir) => ({ type: 'move', uid, to, dir }), equip: (itemUid, targetUid) => ({ type: 'equip', itemUid, targetUid }),
      pickReward: idx => ({ type: 'reward', idx }), useArt: (uid, row, col, dir) => ({ type: 'art', uid, row, col, dir }),
      destroy: uid => ({ type: 'destroy', uid }), freeze: () => ({ type: 'freeze' }) };
    for (const [name, actionOf] of Object.entries(methods)) {
      const original = ps[name].bind(ps);
      ps[name] = (...args) => {
        const a = actionOf(...args), before = this.observe();
        const existing = a.uid ? pieces(before.own).find(p => p.uid === a.uid) : null;
        const id = a.type === 'buy' ? before.own.shop.slots[a.slot]?.id : existing?.id;
        let label = null;
        if (this.teacher && ['buy', 'sell', 'refresh', 'levelUp', 'ready'].includes(a.type)) {
          const cs = this.candidates(before);
          const index = cs.findIndex(c => c.type === a.type && (a.slot === undefined || c.slot === a.slot) && (a.uid === undefined || c.uid === a.uid));
          if (index >= 0) label = { features: encode(before, cs, this.schema), action: index, round: this.m.round };
        }
        const result = original(...args);
        if (result?.ok) {
          const h = this.history, r = h.rounds[this.m.round] ||= {};
          h.actions.push({ ...a, id, round: this.m.round, source: this.teacher ? 'upstream-teacher' : this.source });
          h.totals[a.type] = (h.totals[a.type] || 0) + 1; r[a.type] = (r[a.type] || 0) + 1;
          if (id && ['buy', 'sell'].includes(a.type)) {
            const v = h.byId[id] ||= {}, k = a.type === 'buy' ? 'buys' : 'sells'; v[k] = (v[k] || 0) + 1;
            const byId = r.byId ||= {}, roundId = byId[id] ||= {}; roundId[k] = (roundId[k] || 0) + 1;
          }
          if (a.type === 'refresh') for (const p of pieces(before.own)) {
            const v = h.pieces[p.uid]; v.refreshes++; if (p.area === 'board') v.boardRefreshes++;
          }
          if (this.teacher && ['buy', 'sell', 'refresh', 'levelUp', 'ready'].includes(a.type)) {
            this.steps++; this.history.prepSteps++;
            if (label) this.demos.push(label); else this.demoRejected++;
          }
          const after = this.observe();
          h.actions.at(-1).visiblePiecesAfter = pieces(after.own).map(p => ({ uid: p.uid, id: p.id, area: p.area, row: p.row, col: p.col }));
        } else {
          if (this.teacher && ['buy', 'sell', 'refresh', 'levelUp', 'ready'].includes(a.type)) this.demoRejected++;
          if (!this.teacher && this.source === 'fixed-controller') this.controllerErrors++;
        }
        return result;
      };
    }
    for (const name of ['_applyBand', '_applyCard', 'pickPersonalChoice']) {
      const original = this.m[name].bind(this.m);
      this.m[name] = (player, choice, ...args) => {
        const before = this.observe();
        const result = original(player, choice, ...args);
        if (player === ps && (name !== 'pickPersonalChoice' || result?.ok)) {
          this.history.actions.push({ source: 'fixed-draft-controller', type: name, round: this.m.round,
            choice, visibleOffer: name === '_applyCard' ? before.public.sp : before.own.personalChoice });
          this.observe();
        }
        return result;
      };
    }
  }
  advance() {
    const stop = () => !!this.summary || (this.m.phase === 'PREP' && this.ps.alive && !this.ps.ready);
    if (!this.sched.runUntil(stop, { maxSteps: 1000000 })) throw new Error('Scheduler stalled');
    if (!this.summary) {
      this.observe();
      control(this);
    }
    if (this.engineErrors()) throw new Error(`Engine errors: ${JSON.stringify(this.engineLogErrors)}`);
  }
  engineErrors() { return this.engineLogErrors.length + (this.m?.errorCount || 0) + (this.sched?.errors.length || 0); }
  step(candidateIndex) {
    if (!this.m || this.summary) throw new Error('Reset required');
    const cs = this.candidates();
    if (!Number.isInteger(candidateIndex) || candidateIndex < 0 || candidateIndex >= cs.length) {
      this.actionErrors++; throw new Error('Invalid candidateIndex (no action executed)');
    }
    const a = cs[candidateIndex]; this.source = 'policy';
    const args = a.type === 'buy' ? [a.slot] : a.type === 'sell' ? [a.uid] : a.type === 'ready' ? [true] : [];
    const result = this.ps[a.type === 'ready' ? 'setReady' : a.type](...args);
    if (!result?.ok) { this.actionErrors++; throw new Error(`Candidate rejected: ${JSON.stringify({ a, result })}`); }
    this.steps++; this.history.prepSteps++;
    if (!this.ps.ready && this.history.prepSteps >= this.history.prepBudget) {
      this.budgetForcedReady++; control(this, { forced: true }); this.source = 'budget-controller';
      const ready = this.ps.setReady(true);
      if (!ready?.ok) { this.actionErrors++; throw new Error(`Forced ready failed: ${JSON.stringify(ready)}`); }
    }
    this.advance();
    return this.view();
  }
  view() {
    const observation = this.observe(), candidates = this.candidates(observation);
    const done = !!this.summary;
    if (!done && !candidates.length) throw new Error('No controllable actions');
    return copy({ schema: this.schema, rules: RULES, episode: this.episode, observation, candidates, features: encode(observation, candidates, this.schema),
      reward: done ? (this.summary.victory ? 1 : -1) : 0, done, victory: done && !!this.summary.victory,
      rounds: done ? this.summary.roundsPassed : this.m.round, steps: this.steps, engineErrors: this.engineErrors(),
      actionErrors: this.actionErrors, controllerErrors: this.controllerErrors, controllerBlockedSales: this.controllerBlockedSales,
      budgetForcedReady: this.budgetForcedReady, summary: this.summary,
      controller: 'upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1' });
  }
  demo(options) {
    const terminal = this.reset({ ...options, teacher: true });
    return { schema: this.schema, rules: RULES, terminal, samples: this.demos,
      teacher: 'upstream-bot/rehearsal=0', rejectedLabels: this.demoRejected };
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const pos = process.argv.indexOf('--game');
  const env = await ShopEnv.create(pos >= 0 ? process.argv[pos + 1] : undefined);
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    try {
      const req = JSON.parse(line);
      let result;
      if (req.cmd === 'reset') result = env.reset(req);
      else if (req.cmd === 'step') result = env.step(req.candidateIndex);
      else if (req.cmd === 'demo') result = env.demo(req);
      else if (req.cmd === 'close') { env.close(); console.log(JSON.stringify({ ok: true })); break; }
      else throw new Error('Expected reset/step/demo/close');
      console.log(JSON.stringify({ ok: true, ...result }));
    } catch (e) { console.log(JSON.stringify({ ok: false, error: String(e.stack || e), engineErrors: env.engineErrors(), actionErrors: env.actionErrors || 0 })); }
  }
  env.close();
}
