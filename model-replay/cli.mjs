#!/usr/bin/env node
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const help = 'node model-replay/cli.mjs --trace <eval.json> --checkpoint <model.pt> --out <NEW /tmp/directory> [--game <path>] [--checkpoint-sha256 <hash>] [--max-output-mb <1..256, default 20>]';
const allowed = new Map([['trace', 'trace'], ['checkpoint', 'checkpoint'], ['out', 'out'],
  ['game', 'game'], ['checkpoint-sha256', 'checkpointSha256'], ['max-output-mb', 'maxOutputMb']]);
try {
  if (process.argv.includes('--help')) { console.log(help); process.exit(0); }
  const options = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const flag = process.argv[i], value = process.argv[i + 1], key = allowed.get(flag.slice(2));
    if (!flag.startsWith('--') || !key || !value || value.startsWith('--') || key in options)
      throw new Error(`Invalid or duplicate argument: ${flag}\n${help}`);
    options[key] = value;
  }
  if (!options.trace || !options.checkpoint || !options.out) throw new Error(help);
  // A child process is a local resource watchdog, not another agent. Even synchronous
  // engine callbacks that swallow exceptions cannot outrun the parent wall/RSS limits.
  const child = spawn(process.execPath, ['--max-old-space-size=512',
    fileURLToPath(new URL('./worker.mjs', import.meta.url)), JSON.stringify(options)],
  { stdio: ['ignore', 'inherit', 'inherit'] });
  let failure;
  const stop = message => { failure ||= message; child.kill('SIGKILL'); };
  const deadline = setTimeout(() => stop('25 second wall limit exceeded'), 25_000);
  const monitor = setInterval(() => {
    try {
      const status = fs.readFileSync(`/proc/${child.pid}/status`, 'utf8');
      const rss = Number(status.match(/^VmRSS:\s+(\d+)/m)?.[1] || 0) * 1024;
      const peak = Number(status.match(/^VmHWM:\s+(\d+)/m)?.[1] || 0) * 1024;
      if (Math.max(rss, peak) > 768 * 1024 * 1024) stop('768 MiB RSS limit exceeded');
    } catch (error) { if (error.code !== 'ENOENT') stop(`RSS monitor failed: ${error.message}`); }
  }, 20);
  child.on('error', error => { failure = error.message; });
  child.on('close', code => {
    clearTimeout(deadline); clearInterval(monitor);
    if (failure) console.error(failure);
    process.exitCode = failure ? 1 : code === 0 ? 0 : 1;
  });
} catch (error) { console.error(error.message); process.exitCode = 1; }
