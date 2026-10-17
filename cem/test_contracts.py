"""Independent numerical/accounting expectations; fixtures are NOT game evidence."""
import contextlib
import copy
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from core import CEM, CURVES, TRAIN_SEEDS, accounting, fitness, make_plan, select, validate_parameters, validate_record
from run import Experiment, HERE, REPO, Supervisor, atomic_json, clean_env, group_members, parse_args, proc_memory_bytes


def terminal(seed=20017, win=False):
    return {'config':'solo:ABYSS', 'seed':seed, 'victory':win, 'errors':0,
            'roundsPassed':14 if win else 13, 'bossRound':14, 'hidden':False,
            'players':[{'pid':'ai_0'}],
            'experiment':{'engineRules':'unchanged','forcedSoloBand':None},
            'finalDiagnostics':{'reason':'victory' if win else 'defeat', 'hiddenCleared':False,
                                'players':[{'pid':'ai_0'}], 'bossPool':{'hp':0 if win else 25,'maxHp':100}}}


class Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['node', str(HERE/'guard.mjs'), str(REPO.parent/'Stronghold-Protocol'),
                                 str(REPO.parent/'preview-tools/clean-bandbench.mjs')],
                                env=clean_env(), capture_output=True, text=True, check=True)
        cls.provenance = json.loads(result.stdout)
        cls.schema = cls.provenance['schema']

    def test_numeric_update_has_independent_closed_form(self):
        cem = CEM(self.schema, alpha=.7)
        low = {k:s['min'] for k,s in self.schema.items() if k != 'LEVEL_TARGET'}
        high = {k:s['max'] for k,s in self.schema.items() if k != 'LEVEL_TARGET'}
        low['LEVEL_TARGET'], high['LEVEL_TARGET'] = list(CURVES[0]), list(CURVES[1])
        old = cem.state()
        cem.update([low, high])
        for key in cem.mean:
            self.assertAlmostEqual(cem.mean[key], .3*old['meanNormalized'][key]+.7*.5)
            self.assertAlmostEqual(cem.variance[key], .3*.0625+.7*.25)
        self.assertAlmostEqual(cem.probs[0], .05+.85*(.3/3+.7/2))
        self.assertAlmostEqual(sum(cem.probs), 1)
        for _ in range(100):
            cem.update([low])
        for var in cem.variance.values():
            self.assertAlmostEqual(math.sqrt(var), .05)
        self.assertTrue(all(p >= .05 for p in cem.probs))
        with self.assertRaisesRegex(ValueError, 'No valid elites'):
            cem.update([])

    def test_samples_reproducible_legal_and_policy_parser_validates(self):
        a, b = CEM(self.schema, 31), CEM(self.schema, 31)
        samples = [a.sample() for _ in range(100)]
        self.assertEqual(samples, [b.sample() for _ in range(100)])
        self.assertNotEqual(samples[0], CEM(self.schema, 32).sample())
        for sample in samples:
            validate_parameters(sample, self.schema)
        # The reviewed JS authority must accept generated Python profiles too.
        js = "import fs from 'node:fs'; import {parseProfile} from './policy/lib.mjs'; for(const x of JSON.parse(fs.readFileSync(0,'utf8'))) parseProfile(x);"
        profiles = [{**self.provenance['baseProfile'], 'overrides':s} for s in samples]
        subprocess.run(['node','--input-type=module','-e',js],input=json.dumps(profiles),cwd=REPO,
                       env=clean_env(),text=True,capture_output=True,check=True)

    def test_failure_never_elite_and_victory_beats_auxiliary(self):
        candidates = [{'id':'win','fitness':(1,0)}, {'id':'aux','fitness':(0,1000)},
                      {'id':'failed','fitness':None}]
        self.assertEqual(select(candidates,1)[0]['id'],'win')
        self.assertEqual([c['id'] for c in select(candidates,2)],['win','aux'])
        with self.assertRaisesRegex(ValueError,'Insufficient valid'):
            select([candidates[2]],1)
        with self.assertRaises(ValueError):
            select(candidates,3)
        self.assertEqual(select([{'id':'b','fitness':(0,1)}, {'id':'a','fitness':(0,1)}],1)[0]['id'],'a')

    def test_terminal_loss_and_actual_boss_fraction(self):
        row = terminal()
        outcome = validate_record(row,20017)
        self.assertIs(outcome['victory'],False)
        self.assertEqual(outcome['bossDeductedFraction'],.75)
        self.assertEqual(outcome['auxiliaryFitness'],13.75)
        row['finalDiagnostics']['players'][0]['stats'] = {'bossDamage':999999999,'activatedLayers':999999}
        self.assertEqual(outcome,validate_record(row,20017))
        row['finalDiagnostics']['reason']='eliminated'
        row['finalDiagnostics']['bossPool']=None
        row['roundsPassed']=8
        self.assertEqual(validate_record(row,20017)['auxiliaryFitness'],8)
        for mutate in (lambda r:r.pop('finalDiagnostics'), lambda r:r.update(errors=1),
                       lambda r:r.update(seed=1), lambda r:r.update(victory=True),
                       lambda r:r['finalDiagnostics'].update(reason='error'),
                       lambda r:r['finalDiagnostics']['bossPool'].update(hp=-1),
                       lambda r:r['finalDiagnostics'].pop('bossPool'),
                       lambda r:r['finalDiagnostics'].update(bossPool=None)):
            bad=terminal(); mutate(bad)
            with self.assertRaises(ValueError):
                validate_record(bad,20017)

    def test_denominator_partial_failures_and_training_split(self):
        rows=make_plan(16,8)
        self.assertEqual(len(rows),1056)
        self.assertEqual(sum(r['phase']=='train' for r in rows),1024)
        for g in range(8):
            for c in range(16):
                part=[r for r in rows if r['candidate']==f'g{g:02d}-c{c:02d}']
                self.assertEqual([r['seed'] for r in part],list(range(20017,20025)))
        rows[0].update(status='complete',outcome=validate_record(terminal(win=True),20017))
        rows[1]['status']='timeout'
        rows[2]['status']='failed'
        rows[3]['status']='interrupted'
        report=accounting(rows)['total']
        self.assertEqual(report['planned'],1056)
        self.assertEqual(report['statuses'],{'complete':1,'timeout':1,'failed':1,'interrupted':1,'unrun':1052})
        self.assertEqual(report['observedVictoriesPerPlanned'],1/1056)
        self.assertEqual(report['completeWinRate'],1)
        self.assertFalse(report['allPlannedComplete'])
        self.assertIsNone(fitness(rows[:8]))
        games=[{'seed':s,'status':'complete','outcome':validate_record(terminal(s),s)} for s in TRAIN_SEEDS]
        self.assertEqual(fitness(games),(0,13.75))
        self.assertIsNone(fitness(games[:-1]))
        self.assertIsNone(fitness(games[:-1]+[games[0]]))

    def test_cli_rejects_heldout_and_nested_or_large_local(self):
        for seed in (1,16,90001,90016,100001,100064,20025):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(['--train-seeds',str(seed)])
        for args in (['--parallel','4'],['--server','--parallel','17'],['--server','--parallel','1'],
                     ['--rss-mib','513'],['--output-mib','11'],['--jobs','4'],['--band','band_cannot']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(args)
        result=subprocess.run([sys.executable,'-B',str(HERE/'run.py'),'--run'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('Local training refused',result.stderr)

    def test_environment_strips_injected_loaders_and_runtime(self):
        original=dict(os.environ)
        try:
            os.environ.update(NODE_OPTIONS='--import /untrusted.mjs',ARK_STACKING_RUNTIME='/untrusted',
                              ARK_TEACHER_RUNTIME='/untrusted',ARK_POLICY_PROFILE='/untrusted',LD_PRELOAD='/untrusted')
            self.assertEqual(set(clean_env()) & {'NODE_OPTIONS','ARK_STACKING_RUNTIME','ARK_TEACHER_RUNTIME','ARK_POLICY_PROFILE','LD_PRELOAD'},set())
        finally:
            os.environ.clear(); os.environ.update(original)

    def test_real_timeout_and_process_group_cleanup_without_game(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='.contract-') as tmp:
            sup=Supervisor(Path(tmp),512,10)
            # Real child and grandchild, no simulator. Proves kill/reap rather than mocks.
            script="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)"
            result=sup.launch([sys.executable,'-B','-c',script],clean_env(),Path(tmp)/'process.log',.2)
            self.assertEqual(result['status'],'timeout')
            self.assertTrue(result['cleanup'])
            self.assertTrue(all(not group_members(pid) for pid in sup.processes))
            self.assertLess(result['wallSeconds'],2)

    def test_initial_source_failure_keeps_entire_plan(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='.contract-') as tmp:
            out=Path(tmp)/'run'
            args=parse_args(['--run','--server','--root',str(Path(tmp)/'missing'), '--out',str(out),
                             '--population','2','--elite','1','--generations','1'])
            ex=Experiment(args)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ex.run(),1)
            m=json.loads((out/'manifest.json').read_text())
            self.assertEqual(m['denominator'],48)
            self.assertEqual(m['accounting']['total']['statuses'],{'unrun':48})
            self.assertIn('Source guard',m['error'])
            self.assertTrue(m['resources']['cleanupVerified'])
            self.assertFalse((out/'best.json').exists())

    def test_synthetic_search_freezes_before_dev_and_never_reselects(self):
        class Synthetic(Experiment):
            def game(self,row,profile=None,vanilla=False,expected_profile_hash=None):
                if row['phase']=='dev':
                    saved=json.loads((self.a.out/'manifest.json').read_text())
                    assert saved['searchLearning']['status']=='frozen'
                    assert (self.a.out/'profiles/frozen-best.json').exists()
                # Deliberately make dev default win and frozen lose: never reselect.
                win=row['phase']=='dev' and row['candidate']=='default'
                row.update(status='complete',outcome=validate_record(terminal(row['seed'],win),row['seed']))
        with tempfile.TemporaryDirectory(dir=HERE,prefix='.contract-') as tmp:
            args=parse_args(['--run','--server','--out',str(Path(tmp)/'run'),'--entry',str(REPO.parent/'preview-tools/clean-bandbench.mjs'),
                             '--population','2','--elite','1','--generations','2'])
            ex=Synthetic(args)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ex.run(),0)
            self.assertEqual(ex.manifest['denominator'],64)
            self.assertEqual(ex.manifest['frozen']['candidateId'],'g00-c00')
            self.assertEqual(ex.manifest['devComparison']['default']['victories'],16)
            self.assertEqual(ex.manifest['devComparison']['frozen']['victories'],0)
            self.assertEqual(ex.manifest['frozen']['trainFitness'],(0,13.75))
            self.assertEqual(len(ex.history),2)

    def test_real_sigterm_persists_plan_without_simulation(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='.contract-') as tmp:
            out=Path(tmp)/'run'
            # Run the real signal handler, supervisor and finally persistence.
            script='''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from run import *
class Fault(Experiment):
    def guard(self,label): return {}
    def train(self):
        row=self.rows[0]; row['status']='running'; self.save()
        r=self.supervisor.launch([sys.executable,'-c','import time; print("ready",flush=True); time.sleep(30)'],clean_env(),self.a.out/'ready.log',30)
        row.update(r)
        if r['status']!='complete': raise RuntimeError(r['reason'])
a=parse_args(['--server','--run','--population','2','--elite','1','--generations','1','--out',sys.argv[2]])
sys.exit(Fault(a).run())
'''
            proc=subprocess.Popen([sys.executable,'-B','-c',script,str(HERE),str(out)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    ready=out/'ready.log'
                    if ready.exists() and 'ready' in ready.read_text(): break
                    time.sleep(.01)
                else: self.fail('Fixture child never started')
                proc.send_signal(signal.SIGTERM)
                stdout,stderr=proc.communicate(timeout=5)
                self.assertEqual(proc.returncode,1,stderr)
                m=json.loads((out/'manifest.json').read_text())
                self.assertEqual(m['denominator'],48)
                self.assertEqual(m['accounting']['total']['statuses'],{'interrupted':1,'unrun':47})
                self.assertTrue(m['resources']['cleanupVerified'])
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.wait()

    def test_all_failed_population_cannot_freeze_or_run_dev(self):
        class Failed(Experiment):
            def game(self,row,profile=None,vanilla=False,expected_profile_hash=None):
                assert row['phase']=='train', 'Dev ran without frozen trained candidate'
                row.update(status='timeout',reason='synthetic fault, not a game')
        with tempfile.TemporaryDirectory(dir=HERE,prefix='.contract-') as tmp:
            out=Path(tmp)/'run'
            args=parse_args(['--server','--run','--out',str(out),'--population','2','--elite','1','--generations','1',
                             '--entry',str(REPO.parent/'preview-tools/clean-bandbench.mjs')])
            ex=Failed(args)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ex.run(),1)
            m=json.loads((out/'manifest.json').read_text())
            self.assertEqual(m['accounting']['total']['statuses'],{'timeout':2,'unrun':46})
            self.assertEqual(m['searchLearning']['status'],'failed_unfrozen')
            self.assertNotIn('frozen',m)
            self.assertFalse((out/'best.json').exists())
            self.assertEqual(json.loads((out/'history.json').read_text())[0]['status'],'failed')

    def test_profile_drift_rejected_before_any_process(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='.contract-') as tmp:
            out=Path(tmp)
            args=parse_args(['--mode','smoke','--out',str(out)])
            ex=Experiment(args)
            ex.provenance=self.provenance
            for d in ('raw','profiles','candidates'):
                (out/d).mkdir()
            p=ex.profile('test')
            p.write_text('{}')
            ex.game(ex.rows[0],p,expected_profile_hash='wrong expected hash')
            self.assertEqual(ex.rows[0]['status'],'failed')
            self.assertIn('Profile drift',ex.rows[0]['reason'])
            self.assertEqual(ex.supervisor.processes,[])

    def test_rss_high_water_excludes_pre_exec_launcher_memory(self):
        # Linux ru_maxrss survives exec, whereas VmHWM resets with the address
        # space. Reproduce the actual smoke bookkeeping bug without any game.
        child = "import sys,os,resource,json; sys.path.insert(0,sys.argv[1]); from run import proc_memory_bytes; print(json.dumps({'hwm':proc_memory_bytes(os.getpid(),'VmHWM:'),'legacy':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}))"
        launcher = 'import os,sys; buffer=bytearray(64*1024*1024); os.execv(sys.executable,[sys.executable,"-B","-c",sys.argv[1],sys.argv[2]])'
        result=subprocess.run([sys.executable,'-B','-c',launcher,child,str(HERE)],capture_output=True,text=True,check=True)
        m=json.loads(result.stdout)
        self.assertGreater(m['legacy'],m['hwm']+32*1024*1024)
        self.assertGreater(m['hwm'],0)
        self.assertLess(m['hwm'],64*1024*1024)
        self.assertGreaterEqual(proc_memory_bytes(os.getpid(),'VmHWM:'),proc_memory_bytes(os.getpid()))


if __name__=='__main__':
    unittest.main(verbosity=2)
