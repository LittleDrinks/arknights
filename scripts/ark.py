#!/usr/bin/env python3
"""Project entrypoints; materialize pinned legacy imports outside the source tree."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'configs/layout.json'

def git(*args, cwd=ROOT, **kwargs):
    return subprocess.check_output(['git', '-C', str(cwd), *args], **kwargs)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def catalog():
    data = json.loads(CATALOG.read_text())
    for row in data['files']:
        for key in ('source', 'virtual'):
            p = Path(row[key])
            if p.is_absolute() or '..' in p.parts:
                raise ValueError('Unsafe layout path: ' + str(p))
    return data

def check_game():
    cfg = catalog()
    actual = git('rev-parse', 'HEAD', cwd=ROOT/'game').decode().strip()
    if actual != cfg['rules']:
        raise ValueError('game does not match the pinned rules')
    for relative in ('node_modules', '.cache', 'public/assets', 'public/vendor', 'public/fonts'):
        candidate = ROOT/'game'/relative
        if candidate.exists() or candidate.is_symlink():
            raise ValueError('Generated files must be outside game: '+relative)
    dirty = git('status', '--porcelain', '--untracked-files=all', cwd=ROOT/'game')
    if dirty.strip():
        raise ValueError('game must be pristine; put adaptations/assets/results outside the submodule')
    return actual

def prepare():
    """Retain original relative imports and SHA guards without scattered source clones."""
    cfg = catalog()
    check_game()
    for row in cfg['files']:
        if row['archive']:
            continue
        path = ROOT/row['source']
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != row['sha256']:
            raise ValueError('Source changed; explicitly update its layout identity before running: '+row['source'])
    head = git('rev-parse', 'HEAD').decode().strip()
    key = head[:12]+'-'+digest(CATALOG.read_bytes())[:12]
    target = ROOT/'results/workspaces'/key
    marker = dict(projectCommit=head, catalogSHA256=digest(CATALOG.read_bytes()), sourceBase=cfg['sourceBase'])
    if target.exists():
        if json.loads((target/'.layout.json').read_text()) != marker:
            raise ValueError('Existing workspace does not match the project identity')
        if git('status', '--porcelain', cwd=target).strip():
            raise ValueError('Generated workspace was modified; retain it and prepare a new project revision')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name('.building-'+key+'-'+str(os.getpid()))
    if temp.exists():
        raise ValueError('Interrupted workspace exists: '+str(temp))
    subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout', str(ROOT), str(temp)], check=True)
    subprocess.run(['git', '-C', str(temp), 'read-tree', '--empty'], check=True)
    raw = git('archive', cfg['sourceBase'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe source archive')
            if member.isdir():
                (temp/path).mkdir(parents=True, exist_ok=True)
            else:
                dest = temp/path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive.extractfile(member).read())
                dest.chmod(member.mode)
    for row in cfg['files']:
        dest = temp/row['virtual']
        # Old generated fixtures stay in Git history; they never return to current tracked source.
        if not row['archive']:
            shutil.copyfile(ROOT/row['source'], dest)
        if digest(dest.read_bytes()) != row['sha256']:
            raise ValueError('Workspace source/fixture mismatch: '+row['virtual'])
    (temp/'.layout.json').write_text(json.dumps(marker, indent=2)+'\n')
    subprocess.run(['git', '-C', str(temp), 'add', '-f', '.'], check=True)
    subprocess.run(['git', '-C', str(temp), '-c', 'user.name=Arknights Workspace', '-c',
                    'user.email=workspace@localhost', 'commit', '--quiet', '-m',
                    'Materialize runtime from project '+head], check=True)
    temp.rename(target)
    return target

def output_paths(args):
    for index, argument in enumerate(args):
        flag, separator, inline = argument.partition('=')
        if flag not in ('--out', '--trace-dir', '--out-dir'):
            continue
        value = inline if separator else args[index+1]
        path = Path(value)
        path = (path if path.is_absolute() else ROOT/path).resolve()
        if ROOT/'results' not in path.parents:
            raise ValueError(flag+' must be inside results/')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'prepare', 'env', 'learner', 'ppo', 'analyze', 'replay', 'preview'))
    options, args = parser.parse_known_args()
    if args[:1] == ['--']:
        args = args[1:]
    if options.command == 'check':
        print(json.dumps(dict(game=check_game(), sourceFiles=sum(not r['archive'] for r in catalog()['files']))))
        return
    if options.command == 'preview':
        os.execvp('node', ['node', str(ROOT/'adapters/web/server.mjs'), *args])
    workspace = prepare()
    if options.command == 'prepare':
        print(workspace)
        return
    env = os.environ.copy()
    env.update(STRONGHOLD_GAME=str(ROOT/'game'), ARK_GAME_ROOT=str(ROOT/'game'), PYTHONDONTWRITEBYTECODE='1')
    python = ROOT/'runtime/venvs/learner-20261018/bin/python'
    interpreter = str(python) if python.exists() else sys.executable
    if options.command == 'env':
        coop = '--coop' in args
        if coop:
            args.remove('--coop')
        command = ['node', str(workspace/('coop/env.mjs' if coop else 'rl/env.mjs')), '--game', str(ROOT/'game'), *args]
    elif options.command == 'ppo':
        output_paths(args)
        env.setdefault('COOP_POOL', 'mixed')
        env.setdefault('COOP_SPLIT', 'train')
        if '--env-command' not in args:
            args += ['--env-command', shlex.join(['node', str(workspace/'coop/env.mjs'), '--game', str(ROOT/'game')])]
        if '--mode' not in args:
            args += ['--mode', 'coop']
        command = [interpreter, '-B', str(workspace/'parallel-rl/train.py'), *args]
    elif options.command == 'learner':
        output_paths(args)
        command = [interpreter, '-B', str(workspace/'rl/learner.py'), *args]
    elif options.command == 'analyze':
        output_paths(args)
        command = [interpreter, '-B', str(workspace/'analyze.py'), *args]
    else:
        output_paths(args)
        command = ['node', str(workspace/'frozen-replay/cli.mjs'), '--game', str(ROOT/'game'), *args]
    os.chdir(ROOT)
    os.execvpe(command[0], command, env)

if __name__ == '__main__':
    main()
