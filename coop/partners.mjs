import { ShopEnv } from '../rl/env.mjs';
import { control } from '../rl/controller.mjs';

// Reuse the learner's current-offer legality filter, not a copy of economy rules.
export function legalActions(m, ps) {
  return ShopEnv.prototype.candidates.call({ m, ps, summary: null }, { own: ps.privateView() });
}
function execute(ps, a) {
  const method = a.type === 'ready' ? 'setReady' : a.type;
  const args = a.type === 'buy' ? [a.slot] : a.type === 'sell' ? [a.uid] : a.type === 'ready' ? [true] : [];
  const r = ps[method](...args);
  if (!r?.ok) throw new Error(`Partner action rejected: ${a.type}: ${JSON.stringify(r)}`);
}
export function partnerPrep(env, ps, strategy, rng) {
  // All new shop decisions use only this seat's current view and inherited legal candidates.
  // Fixed deployment/drafts are exactly the solo controller, including no-sale arrange.
  const controller = { m: env.m, ps, bot: env.bot, history: { actions: [] }, controllerBlockedSales: 0 };
  control(controller);
  if (strategy === 'buy2') {
    for (let i = 0; i < 2; i++) {
      const a = legalActions(env.m, ps).find(a => a.type === 'buy');
      if (!a) break;
      execute(ps, a);
    }
  } else if (strategy === 'saver') {
    const level = legalActions(env.m, ps).find(a => a.type === 'levelUp');
    if (level) execute(ps, level);
    const a = legalActions(env.m, ps).filter(a => a.type === 'buy').sort((a, b) => a.price - b.price || a.slot - b.slot)[0];
    if (a) execute(ps, a);
  } else if (strategy === 'random') {
    for (let i = 0; i < 8 && !ps.ready; i++) {
      const actions = legalActions(env.m, ps);
      if (!actions.length) break;
      execute(ps, actions[Math.floor(rng() * actions.length)]);
    }
  } else throw new Error(`Unknown partner strategy: ${strategy}`);
  if (!ps.ready) {
    // Original deadline overflow resolution (not invented resources/teleportation).
    control(controller, { forced: true });
    execute(ps, { type: 'ready' });
  }
  env.partnerBlockedSales += controller.controllerBlockedSales;
}
