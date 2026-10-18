"""Transparent JSONL transport observer: retain full terminal and policy choices.

No response fields are changed. A captured training episode may span multiple
behavior weight versions; it is never attributed to a single frozen checkpoint.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ['--'] else args.command
if not command or not args.out.resolve().is_relative_to(Path('/data1/zsm/arknights')):
    parser.error('A child command and project output directory are required')
args.out.mkdir(parents=True, exist_ok=True)
process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, text=True, bufsize=1)
state, actions = None, []
try:
    for line in sys.stdin:
        request = json.loads(line)
        if request['cmd'] == 'reset':
            actions = []
        elif request['cmd'] == 'step':
            index = request['candidateIndex']
            actions.append(dict(round=state['rounds'], candidateIndex=index, action=state['candidates'][index]))
        process.stdin.write(line)
        process.stdin.flush()
        response = process.stdout.readline()
        if not response:
            raise RuntimeError('Observed environment exited without a response')
        new_state = json.loads(response)
        if new_state.get('ok') and new_state.get('done'):
            seed = new_state['episode']['seed']
            artifact = dict(kind='actual-training-trajectory-not-frozen-model-evaluation',
                            behavior='May span PPO behavior versions; no single-checkpoint attribution',
                            command=command, rules=new_state['rules'], controller=new_state['controller'],
                            budget=new_state['episode']['budget'], actions=actions, terminal=new_state,
                            captureSourceSHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            filename = args.out / f'episode-{os.getpid()}-{seed}.json'
            with filename.open('x') as stream:
                json.dump(artifact, stream)
                stream.write('\n')
        # Preserve exact child response bytes and ordering. No observation,
        # features, rewards, candidate list or engine RNG is edited by the proxy.
        sys.stdout.write(response)
        sys.stdout.flush()
        state = new_state
        if request['cmd'] == 'close':
            break
finally:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
