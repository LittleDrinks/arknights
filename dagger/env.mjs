#!/usr/bin/env node
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import readline from 'node:readline';
import { ShopEnv } from '../rl/env.mjs';
import { query, staticRecords } from './teacher.mjs';
export const TEACHER_HASH = createHash('sha256').update(readFileSync(new URL('./teacher.mjs', import.meta.url))).digest('hex');
export function annotate(state, records) {
  return { ...state, ...query(state.observation, state.candidates, state.features.mask, records), teacherHash: TEACHER_HASH };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const pos = process.argv.indexOf('--game');
  const env = await ShopEnv.create(pos >= 0 ? process.argv[pos + 1] : undefined);
  const records = staticRecords(env.data);
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  try {
    for await (const line of input) {
      try {
        const req = JSON.parse(line);
        let state;
        if (req.cmd === 'reset') {
          if (req.teacher) throw new Error('botPrep teacher mode is forbidden');
          state = env.reset({ ...req, teacher: false });
        } else if (req.cmd === 'step') state = env.step(req.candidateIndex);
        else if (req.cmd === 'close') { console.log(JSON.stringify({ ok: true })); break; }
        else throw new Error('Expected reset/step/close; no demo proxy');
        console.log(JSON.stringify({ ok: true, ...annotate(state, records) }));
      } catch (e) {
        console.log(JSON.stringify({ ok: false, error: String(e.stack || e),
          summary: env.summary || null, engineErrors: env.engineErrors(), actionErrors: env.actionErrors || 0,
          controllerErrors: env.controllerErrors || 0, engineLogErrors: env.engineLogErrors || [],
          schedulerErrors: (env.sched?.errors || []).map(String) }));
      }
    }
  } finally { env.close(); }
}
