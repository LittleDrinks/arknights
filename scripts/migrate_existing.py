#!/usr/bin/env python3
"""One-time in-place migration; rename original bytes and verify every file identity."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess

MOVE = {
 'experiments':'results/runs', 'logs':'results/logs', 'trajectories':'results/trajectories',
 'experiment-code':'results/archive/source/experiment-code-server',
 'code-frozen-replay-v1':'results/archive/snapshots/code-frozen-replay-v1',
 'code-numeric-debug-v1':'results/archive/snapshots/code-numeric-debug-v1',
 'code-ppo-sync-v1':'results/archive/snapshots/code-ppo-sync-v1',
 'code-wide-dev-v1':'results/archive/snapshots/code-wide-dev-v1',
 'tools':'results/archive/launchers/raw-tools', 'research':'results/archive/research',
 'knowledge':'results/datasets/knowledge', 'literature':'results/datasets/literature',
 'cache':'runtime/cache/legacy', 'bootstrap':'runtime/bootstrap', 'venvs':'runtime/venvs',
 'tmp':'runtime/tmp', 'replay-assets.tar.gz':'runtime/cache/archives/replay-assets.tar.gz',
 'benchmark-r0.json':'results/benchmarks/legacy-root/benchmark-r0.json',
 'benchmark-r0.log':'results/benchmarks/legacy-root/benchmark-r0.log',
 'benchmark-r3.json':'results/benchmarks/legacy-root/benchmark-r3.json',
 'benchmark-r3.log':'results/benchmarks/legacy-root/benchmark-r3.log',
 'game':'results/archive/source/game-working-copy',
}
SPECIAL={
 'game/node_modules':'runtime/node_modules', 'game/public/assets':'runtime/web/assets',
 'game/public/vendor':'runtime/web/vendor', 'game/public/fonts':'runtime/web/fonts',
 'game/public/dev':'results/replays/public/dev', 'game/.cache':'runtime/cache/game',
}

def mapped(name):
    for old,new in sorted({**MOVE,**SPECIAL}.items(),key=lambda x:len(x[0]),reverse=True):
        if name==old or name.startswith(old+'/'):
            return new+name[len(old):]
    raise ValueError('Unmapped original file: '+name)

def identity(path):
    s=path.lstat()
    return dict(device=s.st_dev,inode=s.st_ino,bytes=s.st_size,mtimeNS=s.st_mtime_ns,
                mode=stat.S_IFMT(s.st_mode),link=os.readlink(path) if path.is_symlink() else None)

def inventory(root):
    rows={}
    for old in MOVE:
        top=root/old
        if not top.exists() and not top.is_symlink():
            continue
        if top.is_file() or top.is_symlink():
            rows[old]=identity(top)
            continue
        for parent,dirs,files in os.walk(top,followlinks=False):
            for name in list(dirs):
                p=Path(parent)/name
                if p.is_symlink():
                    rows[str(p.relative_to(root))]=identity(p)
                    dirs.remove(name)
            for name in files:
                p=Path(parent)/name
                rows[str(p.relative_to(root))]=identity(p)
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);args=p.parse_args()
    root=args.root.resolve()
    if (root/'.git').exists():raise ValueError('Root is already a repository; migration is one-time')
    unknown={x.name for x in root.iterdir()}-set(MOVE)-{'.migration-stage','results','runtime'}
    if unknown:raise ValueError('Uninventoried top-level entries: '+str(sorted(unknown)))
    stage=root/'.migration-stage/game'
    if not stage.exists():raise ValueError('Pristine staged game clone required')
    if subprocess.check_output(['git','-C',str(stage),'rev-parse','HEAD'],text=True).strip()!='6ab2468279e926d107788f63d7fe219b85f67b4a':raise ValueError('Wrong staged game')
    before=inventory(root)
    out=root/'results/migrations/layout-20261018';out.mkdir(parents=True,exist_ok=False)
    (out/'inventory-before.json').write_text(json.dumps(before,sort_keys=True))
    mapping={name:mapped(name) for name in before}
    (out/'path-map.json').write_text(json.dumps(mapping,sort_keys=True))
    (out/'prefix-map.json').write_text(json.dumps({**MOVE,**SPECIAL},indent=2))
    journal=out/'moves.jsonl'
    for old,new in MOVE.items():
        source=root/old;dest=root/new
        if not source.exists() and not source.is_symlink():continue
        if dest.exists():raise ValueError('Refuse overwrite '+str(dest))
        dest.parent.mkdir(parents=True,exist_ok=True)
        with journal.open('a') as f:f.write(json.dumps(dict(old=old,new=new,state='intent'))+'\n');f.flush();os.fsync(f.fileno())
        source.rename(dest)
        with journal.open('a') as f:f.write(json.dumps(dict(old=old,new=new,state='moved'))+'\n');f.flush();os.fsync(f.fileno())
    archived=root/MOVE['game']
    for old,new in SPECIAL.items():
        source=archived/old.removeprefix('game/');dest=root/new
        if source.exists():
            dest.parent.mkdir(parents=True,exist_ok=True);source.rename(dest)
    stage.rename(root/'game')
    rows={}
    for old,expected in before.items():
        actual=identity(root/mapping[old])
        if actual!=expected:raise ValueError('Preservation mismatch: '+old)
        rows[old]=dict(new=mapping[old],identity=actual)
    (out/'preservation.json').write_text(json.dumps(dict(status='verified',method='same-filesystem rename; inode/device/bytes/mtime/type/link identity retained for every original file',files=len(rows),totalOriginalBytes=sum(x['bytes'] for x in before.values()),at=datetime.now(timezone.utc).isoformat(),entries=rows),sort_keys=True))
    # Shell activation and console entrypoints embed the old prefix. Save each original
    # before rewriting only runtime environment scripts; scientific artifacts stay untouched.
    changes=[]
    for bin_dir in (root/'runtime/venvs').glob('*/bin'):
        for path in bin_dir.iterdir():
            if not path.is_file() or path.is_symlink() or path.stat().st_size>20000:continue
            raw=path.read_bytes();old=str(root/'venvs').encode();new=str(root/'runtime/venvs').encode()
            if old not in raw:continue
            try:raw.decode('utf-8')
            except UnicodeDecodeError:continue
            backup=out/'venv-script-originals'/path.relative_to(root/'runtime/venvs');backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(raw)
            path.write_bytes(raw.replace(old,new));changes.append(str(path.relative_to(root)))
    (out/'runtime-script-updates.json').write_text(json.dumps(changes,indent=2))
    if not (root/'node_modules').exists():(root/'node_modules').symlink_to('runtime/node_modules',target_is_directory=True)
    print(json.dumps(dict(status='verified',originalFiles=len(rows),runtimeScriptsUpdated=len(changes),receipt=str(out/'preservation.json'))))
if __name__=='__main__':main()
