#!/usr/bin/env python3
"""Tiny compatibility check; only two BC labels and four PPO transitions, not training experiments."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ['COOP_CHECK_OUT'])
GAME = os.environ.get('STRONGHOLD_GAME', str(ROOT.parent / 'Stronghold-Protocol'))
ENV = f'node {shlex.quote(str(ROOT / "coop/env.mjs"))} --game {shlex.quote(GAME)}'


def hotstart():
    sys.path.insert(0, str(ROOT / 'rl'))
    from learner import Environment, ActorCritic, bc_update, save_checkpoint, validate_checkpoint, load_checkpoint
    with Environment(f'node {shlex.quote(str(ROOT / "rl/env.mjs"))} --game {shlex.quote(GAME)}', timeout=30) as solo:
        demo = solo.request('demo', seed=7, difficulty='NORMAL')
    assert demo['terminal']['done'] and demo['terminal']['engineErrors'] == 0
    samples = demo['samples'][:2]
    f = samples[0]['features']
    model = ActorCritic(len(f['global']), len(f['candidates'][0]), hidden=8)
    metrics, _ = bc_update(model, samples, epochs=1, batch_size=2)
    assert metrics['parameterDeltaL2'] > 0
    save_checkpoint(OUT / 'solo-bc.pt', model, demo['schema'], demo['rules'], {'algorithm': 'BC', 'samples': 2})
    _, checkpoint = load_checkpoint(OUT / 'solo-bc.pt')
    with Environment(ENV, timeout=30) as coop:
        state = coop.request('reset', seed=7, budget=2)
        validate_checkpoint(checkpoint, state)
        assert state['episode']['mode'] == 'coop'
    print(json.dumps({'soloBCHotstart': True, 'samples': 2, 'parameterDeltaL2': metrics['parameterDeltaL2']}))


def main():
    # Separate Torch processes keep peak RSS bounded; no persistent second model/process.
    subprocess.run([sys.executable, '-B', __file__, 'hotstart'], check=True)
    config = json.loads((ROOT / 'rl/config.json').read_text())
    config.update(hidden=8, epochs=1, batch_size=4, rollout_steps=4, prep_budget=2)
    (OUT / 'config.json').write_text(json.dumps(config))
    cli = [sys.executable, '-B', str(ROOT / 'rl/learner.py'), '--env-command', ENV,
           '--config', str(OUT / 'config.json'), '--seed', '7']
    subprocess.run([*cli, 'demo', '--episodes', '1', '--out', str(OUT / 'coop-demo.jsonl')], check=True)
    rows = [json.loads(line) for line in (OUT / 'coop-demo.jsonl').read_text().splitlines()]
    assert rows[0]['type'] == 'metadata' and any(r['type'] == 'sample' for r in rows)
    episode = next(r for r in rows if r['type'] == 'episode')
    assert episode['mode'] == 'coop' and episode['engineErrors'] == episode['actionErrors'] == 0
    result = subprocess.run([*cli, 'ppo', '--checkpoint', str(OUT / 'solo-bc.pt'), '--out', str(OUT / 'coop-ppo.pt'),
                             '--steps-per-stage', '4', '--curriculum', 'NORMAL'], check=True, capture_output=True, text=True)
    print(result.stdout, end='')
    metrics = next(json.loads(line) for line in result.stdout.splitlines() if 'parameterDeltaL2' in line)
    assert metrics['parameterDeltaL2'] > 0 and metrics['environmentSteps'] == 4
    # Explicit heldout evaluation through unchanged CLI; mode=solo compatibility is exercised here.
    subprocess.run([*cli, 'eval', '--checkpoint', str(OUT / 'coop-ppo.pt'), '--episodes', '1',
                    '--difficulty', 'NORMAL', '--trace-dir', str(OUT / 'eval')], check=True,
                   env={**os.environ, 'COOP_POOL': 'heldout', 'COOP_SPLIT': 'eval', 'COOP_TEAM': 'random-v1,random-v1,random-v1'})
    trace = json.loads((OUT / 'eval/NORMAL-7.json').read_text())
    assert trace['result']['mode'] == 'coop'
    assert trace['result']['partners']['pool'] == 'heldout'
    assert trace['result']['engineErrors'] == trace['result']['actionErrors'] == 0
    print(json.dumps({'demoCLI': True, 'ppoCLI': True, 'heldoutEvalCLI': True, 'artifacts': str(OUT)}))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'hotstart':
        hotstart()
    else:
        main()
