// Two fixed natural seeds; no constructed board/shop/funds, no rerolls or fake combat.
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { stackingReports } from './stacking-runtime.mjs';
const root = resolve(process.env.ARK_STACKING_GAME_ROOT ?? fileURLToPath(new URL('../../Stronghold-Protocol/', import.meta.url)));
const { makeMatch } = await import(pathToFileURL(resolve(root, 'test/match/harness.js')));
const results = [];
for (const seed of [1, 2]) {
  const h = makeMatch({ mode: 'solo', difficulty: 'ABYSS', seed, botRehearsal: 0, captureFrames: false,
    seats: [{ seat: 0, playerId: 'ai_0', name: 'Macro smoke', isBot: true, connected: true }] });
  const rounds = new Map();
  // Observe the module reports as normal scheduler callbacks complete. Never changes game decisions.
  const later = h.m.later.bind(h.m);
  h.m.later = (ms, fn) => later(ms, () => {
    fn();
    const reports = stackingReports(h.ps('ai_0'));
    if (reports.length) rounds.set(h.m.round, structuredClone(reports));
  });
  try {
    h.start();
    const end = h.runToEnd();
    assert.deepEqual(h.logs.error, []);
    const reports = [...rounds.values()].flat();
    assert.ok(reports.length, 'loader must actually invoke the macro');
    assert.ok(reports.every(r => !r.stopReason.startsWith('action-failed:')));
    results.push({ seed, difficulty: 'ABYSS', natural: true, rehearsal: 0,
      victory: end.victory ?? end.win ?? null, finalRound: h.m.round,
      invocations: reports.length, attempted: reports.reduce((n, r) => n + r.attempted, 0),
      accepted: reports.reduce((n, r) => n + r.accepted, 0),
      netCost: reports.reduce((n, r) => n + r.netCost, 0),
      triggeredModules: [...new Set(reports.flatMap(r => r.triggeredModules))],
      errors: h.logs.error.length, peakRssMiB: Math.ceil(process.resourceUsage().maxRSS / 1024) });
  } finally { h.m.dispose(); }
}
console.log(JSON.stringify({ kind: 'natural integration smoke, not a win-rate estimate', results }, null, 2));
