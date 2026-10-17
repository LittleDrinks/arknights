#!/usr/bin/env python3
"""Bounded, sequential, deterministic config screen of ORIGINAL botbench; no selection model.
120s wall / 768MiB summed child RSS / 20MB artifacts, including incomplete cases.
This process and children only write under teacher/. No installs or simulator edits.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
GAME = HERE.parent.parent / 'Stronghold-Protocol'
parser = argparse.ArgumentParser()
parser.add_argument('--cases', default='off:conservative,mixed:aggressive,refresh:conservative,acquire:conservative,refresh:aggressive,acquire:aggressive,mixed:conservative')
parser.add_argument('--seconds', type=float, default=115)
parser.add_argument('--seeds', type=int, default=4)
args = parser.parse_args()
if not 0 < args.seconds <= 120 or not 1 <= args.seeds <= 4:
    parser.error('seconds must be (0,120], seeds 1..4')
cases = [c.split(':') for c in args.cases.split(',')]
if any(len(c) != 2 or c[0] not in ['off', 'mixed', 'refresh', 'acquire'] or c[1] not in ['conservative', 'aggressive'] for c in cases):
    parser.error('invalid variant:budget')
out = HERE / 'results'
out.mkdir(exist_ok=True)
if any(out.iterdir()):
    parser.error('results directory must be empty; preserve earlier evidence before rerunning')
started = time.monotonic()
summary = {'kind': 'heuristic teacher screen, not a model or heldout evaluation', 'cases': []}
def size():
    return sum(p.stat().st_size for p in HERE.rglob('*') if p.is_file())
for variant, budget in cases:
    if time.monotonic() - started >= args.seconds:
        summary['stopped'] = 'wall-budget'
        break
    name = variant + '-' + budget
    env = {**os.environ, 'ARK_TEACHER': variant, 'ARK_TEACHER_BUDGET': budget,
           'ARK_TEACHER_TRACE': str(out / (name + '-trace.json'))}
    env.pop('ARK_STACKING', None)
    env.pop('NODE_OPTIONS', None)
    cmd = ['node', '--max-old-space-size=384', '--loader', str(HERE / 'loader.mjs'),
           '--import', str(HERE / 'observe.mjs'), str(GAME / 'tools/botbench.mjs'),
           '--configs', 'solo:ABYSS', '--seed', '1', '--seeds', str(args.seeds),
           '--rehearsal', '0', '--jobs', '1', '--json', str(out / (name + '.json'))]
    peak = 0
    stopped = None
    with open(out / (name + '.log'), 'w') as log:
        p = subprocess.Popen(cmd, cwd=HERE.parent, env=env, stdout=log, stderr=log, start_new_session=True)
        while p.poll() is None:
            # Node worker threads share this RSS; botbench jobs=1 has no child processes.
            try:
                rss = int((Path('/proc') / str(p.pid) / 'statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
                peak = max(peak, rss)
            except (FileNotFoundError, ProcessLookupError):
                pass
            if time.monotonic() - started >= args.seconds:
                stopped = 'wall-budget'
            elif peak + 32 * 1024**2 > 768 * 1024**2:
                stopped = 'rss-budget'
            elif size() > 19 * 1024**2:
                stopped = 'output-budget'
            if stopped:
                os.killpg(p.pid, signal.SIGKILL)
                break
            time.sleep(0.05)
        code = p.wait()
    row = {'variant': variant, 'budget': budget, 'returncode': code, 'peakRSSBytes': peak,
           'status': stopped or ('complete' if code == 0 else 'failed'), 'command': cmd}
    result = out / (name + '.json')
    if code == 0 and result.exists():
        records = json.loads(result.read_text())
        row.update(games=len(records), wins=sum(r['victory'] for r in records),
                   errors=sum(r['errors'] for r in records), rounds=[r['roundsPassed'] for r in records])
    summary['cases'].append(row)
    print(json.dumps(row), flush=True)
    if stopped:
        summary['stopped'] = stopped
        break
summary['elapsedSeconds'] = time.monotonic() - started
summary['artifactBytes'] = size()
summary['unexecuted'] = cases[len(summary['cases']):]
(out / 'screen.json').write_text(json.dumps(summary, indent=2) + '\n')
