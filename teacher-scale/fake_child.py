"""Synthetic subprocess only: never imports game or learner/model code."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from fixture import demo

mode = sys.argv[1]
for line in sys.stdin.buffer:
    req = json.loads(line)
    if req['cmd'] == 'close':
        if mode == 'bad-close':
            print('{"ok":false}', flush=True)
        else:
            print('{"ok":true}', flush=True)
        if mode == 'trailing':
            print('trailing', flush=True)
        break
    if mode == 'hang':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
        Path(os.environ['FAKE_PID']).write_text(str(child.pid))
        time.sleep(10)
    elif mode == 'flood':
        os.write(1, b'x'*32768)
    elif mode == 'partial':
        os.write(1, b'{"ok":')
        break
    elif mode == 'malformed':
        print('{bad-json}', flush=True)
    elif mode == 'error':
        print(json.dumps(dict(ok=False, error='synthetic-error-'+'x'*7000)), flush=True)
    else:
        if mode == 'slow':
            time.sleep(.08)
        if mode == 'stderr':
            os.write(2, b'synthetic stderr\n')
        result = demo(req['seed'], os.environ.get('COOP_POOL', 'normal'),
                      alive=mode != 'carry', rejected=1 if mode == 'rejection' else 0)
        print(json.dumps(result), flush=True)
