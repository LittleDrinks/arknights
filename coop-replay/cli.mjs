#!/usr/bin/env node
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { limits } from './replay.mjs';

const help = `node coop-replay/cli.mjs --trace <training.json> --out <NEW directory>
  [--mode verify|record] [--prefix <action count; verify only>] [--game <path>] [--trace-sha256 <hex>]
  [--seconds 12] [--rss-mb 768] [--heap-mb 512] [--max-output-mb 12] [--frame-every 3]
Default: action replay only; no battle recordings. Full record mode is intended for server use.`;
const flags = { trace: 'trace', out: 'out', mode: 'mode', game: 'game', 'trace-sha256': 'traceSha256',
  prefix: 'prefix', seconds: 'seconds', 'rss-mb': 'rssMb', 'heap-mb': 'heapMb', 'max-output-mb': 'maxOutputMb', 'frame-every': 'frameEvery' };
try {
  if (process.argv.includes('--help')) { console.log(help); process.exit(0); }
  const options = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const flag = process.argv[i], value = process.argv[i + 1], key = flags[flag.slice(2)];
    if (!flag.startsWith('--') || !key || !value || value.startsWith('--') || key in options) throw new Error(help);
    options[key] = value;
  }
  if (!options.trace || !options.out) throw new Error(help);
  const budget = limits(options);
  if (budget.heapMb >= budget.rssMb) throw new Error('Heap budget must be below RSS budget');
  const child = spawn(process.execPath, [`--max-old-space-size=${budget.heapMb}`,
    fileURLToPath(new URL('./worker.mjs', import.meta.url)), JSON.stringify(options)],
  { stdio: ['ignore', 'inherit', 'inherit', 'ipc'] });
  let failure, progress;
  child.on('message', value => { progress = value; });
  const stop = message => { failure ||= message; child.kill('SIGKILL'); };
  const deadline = setTimeout(() => stop('Wall time budget exceeded'), budget.seconds * 1000);
  const monitor = setInterval(() => {
    try {
      const status = fs.readFileSync(`/proc/${child.pid}/status`, 'utf8');
      const rss = Number(status.match(/^VmRSS:\s+(\d+)/m)?.[1] || 0);
      const peak = Number(status.match(/^VmHWM:\s+(\d+)/m)?.[1] || 0);
      if (!rss || !peak) throw new Error('RSS counters unavailable');
      if (Math.max(rss, peak) * 1024 > budget.rssMb * 1024 ** 2) stop('RSS budget exceeded');
    } catch (e) { if (e.code !== 'ENOENT' && e.code !== 'ESRCH') stop(`RSS monitor: ${e.message}`); }
  }, 20);
  child.on('error', e => { failure = e.message; });
  child.on('close', code => {
    clearTimeout(deadline); clearInterval(monitor);
    if (failure) console.error(JSON.stringify({ error: failure, lastProgress: progress }));
    process.exitCode = failure || code !== 0 ? 1 : 0;
  });
} catch (e) { console.error(e.message); process.exitCode = 1; }
