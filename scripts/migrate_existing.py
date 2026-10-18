#!/usr/bin/env python3
"""One-time journaled migration: preserve original bytes, refuse collisions, resume moves."""
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
RULES='6ab2468279e926d107788f63d7fe219b85f67b4a'

def exists(path):return path.exists() or path.is_symlink()

def mapped(name):
    for old,new in sorted({**MOVE,**SPECIAL}.items(),key=lambda x:len(x[0]),reverse=True):
        if name==old or name.startswith(old+'/'):return new+name[len(old):]
    if name.split('/')[0] in ('results','runtime'):return name
    raise ValueError('Unmapped original file: '+name)

def identity(path):
    s=path.lstat()
    return dict(device=s.st_dev,inode=s.st_ino,bytes=s.st_size,mtimeNS=s.st_mtime_ns,
                mode=stat.S_IFMT(s.st_mode),link=os.readlink(path) if path.is_symlink() else None)

def node_identity(path):
    d=identity(path)
    return {k:d[k] for k in ('device','inode','mode','link')} if stat.S_ISDIR(d['mode']) else d

def inventory(root):
    rows={}
    for old in (*MOVE,'results','runtime'):
        top=root/old
        if not exists(top):continue
        if not top.is_dir() or top.is_symlink():rows[old]=identity(top);continue
        for parent,dirs,files in os.walk(top,followlinks=False):
            for name in list(dirs):
                p=Path(parent)/name
                if p.is_symlink():rows[str(p.relative_to(root))]=identity(p);dirs.remove(name)
            for name in files:
                p=Path(parent)/name;rows[str(p.relative_to(root))]=identity(p)
    return rows

def plain_parents(root,path):
    for parent in (path,*path.parents):
        if parent==root:break
        if parent.is_symlink():raise ValueError('Destination crosses a symlink: '+str(parent))
        if exists(parent) and not parent.is_dir():raise ValueError('Destination parent is not a directory: '+str(parent))

def staged_game(stage):
    if not stage.is_dir() or stage.is_symlink():raise ValueError('Pristine staged game clone required')
    def git(*args):return subprocess.check_output(['git','-C',str(stage),*args],text=True).strip()
    if git('rev-parse','--show-toplevel')!=str(stage):raise ValueError('Stage is not its own checkout')
    if git('rev-parse','HEAD')!=RULES:raise ValueError('Wrong staged game')
    if git('status','--porcelain','--untracked-files=all'):raise ValueError('Dirty staged game')
    for rel in ('node_modules','.cache','public/assets','public/vendor','public/fonts'):
        if exists(stage/rel):raise ValueError('Generated content in staged game: '+rel)

def make_plan(root,stage):
    staged_game(stage)
    operations=[]
    for old,new in MOVE.items():
        if exists(root/old):operations.append(dict(source=old,target=new,identity=node_identity(root/old)))
    for old,new in SPECIAL.items():
        if exists(root/old):operations.append(dict(source=MOVE['game']+'/'+old.removeprefix('game/'),target=new,identity=node_identity(root/old)))
    operations.append(dict(source=str(stage.relative_to(root)),target='game',identity=node_identity(stage)))
    # Check EVERY destination before moving anything; existing runtime/results data stays intact.
    for op in operations:
        target=root/op['target'];plain_parents(root,target.parent)
        if exists(target) and op['target']!='game':raise ValueError('Refuse existing destination '+str(target))
    if exists(root/'node_modules'):raise ValueError('Refuse existing root node_modules')
    return operations

def write_new(path,value):
    with path.open('x') as f:json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())

def move_one(root,op,journal):
    source,target=root/op['source'],root/op['target']
    plain_parents(root,target.parent)
    if exists(target):
        if node_identity(target)!=op['identity']:raise ValueError('Resume target identity mismatch: '+op['target'])
        if exists(source) and node_identity(source)==op['identity']:raise ValueError('Source and destination both own the original object')
        return  # Rename completed before its journal acknowledgement.
    if not exists(source) or node_identity(source)!=op['identity']:raise ValueError('Missing/changed migration source: '+op['source'])
    target.parent.mkdir(parents=True,exist_ok=True)
    def log(state):
        with journal.open('a') as f:f.write(json.dumps(dict(source=op['source'],target=op['target'],state=state))+'\n');f.flush();os.fsync(f.fileno())
    log('intent');source.rename(target);log('moved')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--resume',action='store_true');args=p.parse_args()
    root=args.root.resolve();out=root/'results/migrations/layout-20261018'
    if (root/'.git').exists():raise ValueError('Root is already a repository; migration is one-time')
    if args.resume:
        if (out/'preservation.json').exists():print('File preservation already verified; finish project deployment separately.');return
        operations=json.loads((out/'operations.json').read_text());before=json.loads((out/'inventory-before.json').read_text());mapping=json.loads((out/'path-map.json').read_text())
    else:
        unknown={x.name for x in root.iterdir()}-set(MOVE)-{'.migration-stage','results','runtime'}
        if unknown:raise ValueError('Uninventoried top-level entries: '+str(sorted(unknown)))
        if exists(out):raise ValueError('Migration exists; inspect it and use --resume')
        operations=make_plan(root,root/'.migration-stage/game');before=inventory(root);mapping={name:mapped(name) for name in before}
        out.mkdir(parents=True,exist_ok=False)
        write_new(out/'inventory-before.json',before);write_new(out/'path-map.json',mapping);write_new(out/'prefix-map.json',{**MOVE,**SPECIAL});write_new(out/'operations.json',operations)
    for op in operations:move_one(root,op,out/'moves.jsonl')
    rows={}
    for old,expected in before.items():
        actual=identity(root/mapping[old])
        if actual!=expected:raise ValueError('Preservation mismatch: '+old)
        rows[old]=dict(new=mapping[old],identity=actual)
    write_new(out/'preservation.json',dict(status='verified',method='same-filesystem rename; inode/device/bytes/mtime/type/link identity retained for every original file',files=len(rows),totalOriginalBytes=sum(x['bytes'] for x in before.values()),at=datetime.now(timezone.utc).isoformat(),entries=rows))
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
    write_new(out/'runtime-script-updates.json',changes)
    if not exists(root/'node_modules'):(root/'node_modules').symlink_to('runtime/node_modules',target_is_directory=True)
    print(json.dumps(dict(status='verified',originalFiles=len(rows),runtimeScriptsUpdated=len(changes),receipt=str(out/'preservation.json'))))
if __name__=='__main__':main()
