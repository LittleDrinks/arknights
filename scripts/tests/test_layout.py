import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import ark
import migrate_existing as M

class Migration(unittest.TestCase):
    def fixture(self,base):
        root=Path(base)/'project';root.mkdir()
        for rel,data in [('experiments/a.json','original'),('game/file.txt','upstream'),('.migration-stage/game/file.txt','clean')]:
            p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)
        return root
    def run_main(self,root,resume=False):
        with patch.object(sys,'argv',['migrate','--root',str(root)]+(['--resume'] if resume else [])),patch.object(M,'staged_game'),contextlib.redirect_stdout(io.StringIO()):M.main()
    def test_normal_preserves_every_file_and_cleans_root(self):
        with tempfile.TemporaryDirectory() as t:
            r=self.fixture(t);before=M.identity(r/'experiments/a.json');self.run_main(r)
            self.assertEqual(M.identity(r/'results/runs/a.json'),before)
            self.assertEqual((r/'game/file.txt').read_text(),'clean')
            self.assertEqual((r/'results/archive/source/game-working-copy/file.txt').read_text(),'upstream')
            self.assertEqual(json.loads((r/'results/migrations/layout-20261018/preservation.json').read_text())['status'],'verified')
    def test_special_collision_rejected_before_any_move(self):
        with tempfile.TemporaryDirectory() as t:
            r=self.fixture(t);(r/'game/.cache').write_text('source');p=r/'runtime/cache/game';p.parent.mkdir(parents=True);p.write_text('keep')
            with self.assertRaisesRegex(ValueError,'existing destination'):self.run_main(r)
            self.assertEqual(p.read_text(),'keep');self.assertTrue((r/'experiments/a.json').exists())
    def test_dangling_source_link_is_preserved(self):
        with tempfile.TemporaryDirectory() as t:
            r=self.fixture(t);p=r/'game/public/assets';p.parent.mkdir();p.symlink_to('missing-assets');before=M.identity(p)
            self.run_main(r);self.assertEqual(M.identity(r/'runtime/web/assets'),before)
    def test_dangling_target_link_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            r=self.fixture(t);p=r/'runtime/cache/game';p.parent.mkdir(parents=True);p.symlink_to('missing')
            (r/'game/.cache').write_text('source')
            with self.assertRaises(ValueError):self.run_main(r)
            self.assertEqual(os.readlink(p),'missing');self.assertTrue((r/'experiments/a.json').exists())
    def test_file_or_symlink_destination_parent_rejected(self):
        for kind in ('file','link'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as t:
                r=self.fixture(t);(r/'game/public').mkdir();(r/'game/public/assets').write_text('source');(r/'runtime').mkdir()
                p=r/'runtime/web';p.write_text('keep') if kind=='file' else p.symlink_to('missing')
                with self.assertRaises(ValueError):self.run_main(r)
                self.assertTrue((r/'experiments/a.json').exists())
    def test_resume_after_rename_before_ack(self):
        for stop in ('first','last'):
            with self.subTest(stop=stop),tempfile.TemporaryDirectory() as t:
                r=self.fixture(t);original=M.move_one;count=0
                def interrupt(root,op,journal):
                    nonlocal count
                    original(root,op,journal);count+=1
                    if count==1 and stop=='first' or op['target']=='game' and stop=='last':raise RuntimeError('simulated interruption')
                with patch.object(M,'move_one',interrupt),self.assertRaisesRegex(RuntimeError,'simulated'):self.run_main(r)
                self.run_main(r,resume=True)
                self.assertEqual((r/'results/runs/a.json').read_text(),'original')
                self.assertEqual((r/'game/file.txt').read_text(),'clean')
    def test_real_staged_checkout_must_be_clean(self):
        with tempfile.TemporaryDirectory() as t:
            stage=Path(t)/'game'
            subprocess.run(['git','clone','--quiet','--shared',str(ROOT/'game'),str(stage)],check=True,capture_output=True)
            M.staged_game(stage)
            (stage/'extra.txt').write_text('dirty')
            with self.assertRaisesRegex(ValueError,'Dirty staged'):M.staged_game(stage)

class Outputs(unittest.TestCase):
    def test_all_explicit_and_abbreviated_escapes_rejected(self):
        for command,args in [('analyze',['--out-d','/tmp/o']),('analyze',['--out-dir=/tmp/o']),('ppo',['--ou','/tmp/o']),('ppo',['--out',str(ROOT/'results/runs/a'),'--out','/tmp/o']),('learner',['--trace-dir','/tmp/o'])]:
            with self.subTest(command=command,args=args),self.assertRaises(ValueError):ark.output_paths(args,command)
        ark.output_paths(['--out='+str(ROOT/'results/runs/a')],'ppo')
    def test_analysis_default_is_explicitly_inside_results(self):
        with patch.object(sys,'argv',['ark','analyze','--baseline','baseline.json']),patch.object(ark,'prepare',return_value=Path('/tmp/fake')),patch.object(ark.os,'chdir'),patch.object(ark.os,'execvpe') as execute:
            ark.main()
        args=execute.call_args[0][1];index=args.index('--out-dir');self.assertTrue(Path(args[index+1]).is_relative_to(ROOT/'results/benchmarks'))

if __name__=='__main__':unittest.main()
