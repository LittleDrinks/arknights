#!/usr/bin/env node
import { runCLI } from '../coop-replay/cli.mjs';
import { PROFILE } from './profile.mjs';

runCLI({ profile: PROFILE, usage: `node frozen-replay/cli.mjs
  --trace <frozen-seed65.json> --trace-sha256 <explicit SHA256>
  --receipt <main-reproduction.json> --receipt-sha256 <explicit SHA256>
  --out <NEW isolated directory> [--game <path>] [--mode verify|record]
  [--prefix <action count; verify only>]
  [--seconds 12] [--rss-mb 768] [--heap-mb 512] [--max-output-mb 12] [--frame-every 3]
Frozen BC development team victory; learner eliminated R12, individual victory=false.
Saved-action replay only. No model inference or training. No live index publishing.` });
