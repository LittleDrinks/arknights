#!/usr/bin/env node
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';
import { createHash } from 'node:crypto';
import { ShopEnv } from '../rl/env.mjs';
import { MANIFEST, MANIFEST_SHA256, poolConfig, selectTeam, localRng, uint32 } from './pool.mjs';
import { partnerPrep } from './partners.mjs';
import { capacityFilteredBot } from './controller.mjs';

const copy = x => JSON.parse(JSON.stringify(x));
export class CoopEnv extends ShopEnv {
  static async create(game, config = poolConfig()) {
    // Parent create owns the rule pin, clean-rules check and engine imports. It constructs no Match.
    const base = await ShopEnv.create(game);
    return new CoopEnv({ Match: base.Match, VirtualScheduler: base.VirtualScheduler, data: base.data, bot: base.bot }, config);
  }
  constructor(deps, config) {
    super(deps); this.poolConfig = config;
    this.bot = capacityFilteredBot(deps.bot, () => { this.controllerBlockedMoves++; });
  }
  reset({ seed = 1, mode = 'coop', difficulty = 'NORMAL', budget = 128, teacher = false } = {}) {
    // Existing learner.evaluate sends solo literally. Alias only; Match is ALWAYS coop.
    if (!['coop', 'solo'].includes(mode)) throw new Error('Expected coop (solo accepted only as learner compatibility alias)');
    uint32(seed, 'seed');
    if (!['NORMAL', 'HARD', 'ABYSS'].includes(difficulty) || !Number.isInteger(budget) || budget < 1 || budget > 1024 ||
        typeof teacher !== 'boolean') throw new Error('Invalid difficulty/budget/teacher');
    this.close();
    // Small reset-only copy of the parent's orchestration; all observation/action/feature semantics inherited.
    Object.assign(this, { summary: null, actionErrors: 0, controllerErrors: 0, controllerBlockedSales: 0,
      budgetForcedReady: 0, engineLogErrors: [], steps: 0, prepRound: 0, lastBondState: null,
      teacher, source: 'fixed-controller', demos: [], demoRejected: 0, partnerBlockedSales: 0, partnerActionErrors: 0,
      controllerBlockedMoves: 0, rejectedActions: [] });
    const team = selectTeam(this.poolConfig, seed);
    this.episode = { seed, mode: 'coop', difficulty, budget, partners: {
      manifest: MANIFEST.version, manifestSHA256: MANIFEST_SHA256, ...this.poolConfig,
      seats: team.map((p, i) => ({ seat: i + 1, id: p.id, family: p.family })) } };
    this.history = { actions: [], bonds: [], pieces: {}, totals: {}, rounds: {}, byId: {}, prepSteps: 0, prepBudget: budget };
    this.partnerAudit = {};
    this.sched = new this.VirtualScheduler({ onError: () => {} });
    this.m = new this.Match({ roomCode: 'COOP', mode: 'coop', difficulty, seed, data: this.data, scheduler: this.sched,
      botRehearsal: 0, botRehearsalDelegate: false, clientCombat: false,
      seats: [0, 1, 2, 3].map(seat => ({ seat, playerId: seat ? `partner-${seat}` : 'learner',
        name: seat ? `Partner ${seat}` : 'Learner', isBot: true, connected: true })),
      send: () => true, broadcast() {}, onEnd: s => { this.summary = copy(s); },
      log: { info() {}, debug() {}, warn() {}, error: (...x) => this.engineLogErrors.push(x.map(String).join(' ')) } });
    this.ps = this.m.players.get('learner');
    this.installRecorder();
    for (const method of ['move', 'equip', 'pickReward', 'destroy']) {
      const original = this.ps[method].bind(this.ps);
      this.ps[method] = (...args) => {
        const r = original(...args);
        if (!r?.ok) this.rejectedActions.push({ round: this.m.round, phase: this.m.phase, method, args, result: r });
        return r;
      };
    }
    this.installPartners(team);
    this.m.start();
    if (teacher) {
      if (!this.sched.runUntil(() => !!this.summary, { maxSteps: 1000000 })) throw new Error('Coop teacher did not terminate');
    } else this.advance();
    this.assertHealthy();
    return this.view();
  }
  installPartners(team) {
    const m = this.m, upstream = m.scheduleBotPrep.bind(m);
    for (const ps of m.order.slice(1)) {
      const audit = this.partnerAudit[ps.playerId] = { rounds: {}, actions: 0, rejected: 0, hash: createHash('sha256') };
      for (const method of ['buy', 'sell', 'refresh', 'levelUp', 'setReady', 'move', 'equip', 'pickReward', 'useArt', 'destroy', 'freeze']) {
        const original = ps[method].bind(ps);
        ps[method] = (...args) => {
          const r = original(...args);
          const row = audit.rounds[m.round] ||= { scheduled: 0, ready: 0, actions: {} };
          if (r?.ok) {
            audit.actions++;
            row.actions[method] = (row.actions[method] || 0) + 1;
            if (method === 'setReady' && args[0]) row.ready++;
            audit.hash.update(JSON.stringify([m.round, method, args]) + '\n');
          } else { audit.rejected++; this.partnerActionErrors++; this.actionErrors++; }
          return r;
        };
      }
    }
    m.scheduleBotPrep = (ps, i = 0) => {
      if (ps === this.ps) {
        if (this.teacher) return upstream(ps, i);
        return; // Never schedule original bot economy on the primary policy seat.
      }
      const member = team[ps.seat - 1], round = m.round;
      const audit = this.partnerAudit[ps.playerId];
      const row = audit.rounds[round] ||= { scheduled: 0, ready: 0, actions: {} };
      if (row.scheduled++) throw new Error(`Duplicate partner prep: ${ps.playerId}/${round}`);
      if (member.strategy === 'original') return upstream(ps, i);
      // Guard stale callbacks just as the original scheduler does; execute once per live PREP.
      m.later(i, () => {
        if (m.phase !== 'PREP' || m.round !== round || !ps.alive || ps.ready) return;
        partnerPrep(this, ps, member.strategy, localRng(this.poolConfig.poolSeed, `actions/${this.episode.seed}/${ps.seat}/${round}`));
      });
    };
  }
  assertHealthy() {
    if (this.engineErrors() || this.actionErrors || this.controllerErrors) {
      throw new Error(`Invalid coop rollout: engine=${this.engineErrors()} action=${this.actionErrors} controller=${this.controllerErrors} rejected=${JSON.stringify(this.rejectedActions)}`);
    }
    if (this.summary && (typeof this.summary.victory !== 'boolean' || this.summary.players?.length !== 4 || this.summary.errors)) {
      throw new Error('Incomplete/invalid coop summary');
    }
  }
  view() {
    this.assertHealthy();
    const v = super.view();
    v.controller = `coop/visible-capacity-filter-v1+${v.controller}`;
    // Never return a teammate's private offers/actions while the learner can still act.
    if (v.done) {
      v.partnerAudit = Object.fromEntries(Object.entries(this.partnerAudit).map(([id, a]) =>
        [id, { rounds: copy(a.rounds), actions: a.actions, rejected: a.rejected, actionSHA256: a.hash.copy().digest('hex') }]));
      v.controllerBlockedMoves = this.controllerBlockedMoves;
      v.partnerBlockedSales = this.partnerBlockedSales;
    }
    return v;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const pos = process.argv.indexOf('--game');
  if (pos >= 0 && !process.argv[pos + 1]) throw new Error('--game requires a path');
  const env = await CoopEnv.create(pos >= 0 ? process.argv[pos + 1] : undefined);
  try {
    const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    for await (const line of lines) {
      try {
        const req = JSON.parse(line);
        let result;
        if (req.cmd === 'reset') result = env.reset(req);
        else if (req.cmd === 'step') result = env.step(req.candidateIndex);
        else if (req.cmd === 'demo') result = env.demo(req);
        else if (req.cmd === 'close') { console.log(JSON.stringify({ ok: true })); break; }
        else throw new Error('Expected reset/step/demo/close');
        const encoded = JSON.stringify({ ok: true, ...result });
        if (Buffer.byteLength(encoded) > 20_000_000) throw new Error('Response exceeds 20 MB');
        console.log(encoded);
      } catch (e) {
        console.log(JSON.stringify({ ok: false, error: String(e.stack || e), engineErrors: env.engineErrors(), actionErrors: env.actionErrors || 0 }));
      }
    }
  } finally { env.close(); }
}
