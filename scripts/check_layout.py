#!/usr/bin/env python3
"""Validate source identities, frozen submodule and generated-output boundaries."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
from ark import ROOT, catalog, check_game
cfg=catalog()
check_game()
subprocess.run(['git','-C',str(ROOT),'diff','--check'],check=True)
tracked=subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z']).decode().split('\0')
for name in tracked:
    if not name:
        continue
    if name.startswith(('results/','runtime/')) and Path(name).name!='.gitkeep':
        raise ValueError('Generated file is tracked: '+name)
for row in cfg['files']:
    if not row['archive']:
        p=ROOT/row['source']
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('Relocated source changed: '+row['source'])
modules=subprocess.check_output(['git','-C',str(ROOT),'ls-files','--stage','game']).decode()
if not modules.startswith('160000 '+cfg['rules']):
    raise ValueError('game is not the pinned gitlink')
for folder in ('runs','benchmarks','tests','logs','trajectories','replays','archive','migrations','workspaces'):
    subprocess.run(['git','-C',str(ROOT),'check-ignore','--quiet','results/'+folder+'/example.json'],check=True)
print(json.dumps(dict(status='pass',game=cfg['rules'],sourceFiles=sum(not r['archive'] for r in cfg['files']),generatedResultsTracked=False)))
