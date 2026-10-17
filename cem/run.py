#!/usr/bin/env python3
"""Linux, standard library only. Dry-run by default; all games are subprocesses."""
import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time

from core import CEM, TRAIN_SEEDS, DEV_SEEDS, accounting, fitness, make_plan, select, validate_record

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MIB = 1024*1024


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_env():
    # Never inherit loaders, NODE_OPTIONS, ARK_* runtimes, dynamic-linker hooks,
    # profile selection or seed overrides from the invoking shell.
    return {k:v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TZ')}


def atomic_json(path, obj):
    data = (json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode()
    tmp = path.with_suffix(path.suffix+'.tmp')
    with tmp.open('wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def group_members(pgid):
    groups = pgid if isinstance(pgid, set) else {pgid}
    found = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p/'stat').read_text().rsplit(')', 1)[1].split()
            if int(stat[2]) in groups and stat[0] != 'Z':
                found.append(int(p.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    return found


def proc_memory_bytes(pid, field='VmRSS:'):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith(field):
                return int(line.split()[1])*1024
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0


class Supervisor:
    def __init__(self, out, rss_mib, output_mib, local=False):
        self.out, self.rss_limit, self.output_limit = out, rss_mib*MIB, output_mib*MIB
        self.local = local
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.active = {}
        self.reason = None
        self.deadline = None
        self.started = None
        self.last_sim_end = None
        self.peak_rss = 0
        self.max_child_rss = 0
        self.cleanup = True
        self.processes = []
        self.last_check = 0
        self.last_disk_check = 0

    def cancel(self, reason):
        with self.lock:
            if not self.reason:
                self.reason = reason
            self.stop.set()

    def check(self):
        with self.lock:
            now = time.monotonic()
            if now-self.last_check < .02:
                return self.reason
            self.last_check = now
            pids = {os.getpid()}
            pids.update(group_members(set(self.active)))
            rss = sum(proc_memory_bytes(pid) for pid in pids)
            self.peak_rss = max(self.peak_rss, rss)
            # Conservative 64 MiB headroom below the requested ceiling.
            if rss >= self.rss_limit-64*MIB:
                self.cancel('rss_budget')
            if now-self.last_disk_check >= .25:
                self.last_disk_check = now
                size = 0
                for p in self.out.rglob('*'):
                    try:
                        if p.is_file():
                            size += p.stat().st_size
                    except FileNotFoundError:  # atomic manifest rename in another worker
                        pass
                if size >= self.output_limit-max(MIB,len(self.active)*262144):
                    self.cancel('output_budget')
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.cancel('local_sim_budget')
            return self.reason

    def launch(self, command, env, log, timeout, simulation=False):
        start = time.monotonic()
        if self.stop.is_set():
            return {'status':'unrun', 'reason':self.reason, 'cleanup':True}
        if simulation and self.local and self.started is None:
            self.started = start
            # Includes startup, between-game bookkeeping, termination and reap.
            self.deadline = start+7.5
        proc = None
        reason, status, exit_code, cleaned = None, 'failed', None, True
        try:
            with log.open('wb') as f:
                proc = subprocess.Popen([sys.executable, '-B', str(HERE/'limit_exec.py'), *command],
                                        cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=f, stderr=f,
                                        start_new_session=True)
                with self.lock:
                    self.active[proc.pid] = proc
                    self.processes.append(proc.pid)
                while proc.poll() is None:
                    self.check()
                    if self.stop.is_set():
                        reason = self.reason
                        status = 'timeout' if reason == 'local_sim_budget' else 'interrupted'
                        break
                    if time.monotonic()-start >= timeout:
                        reason, status = 'per_game_timeout', 'timeout'
                        break
                    self.stop.wait(.02)
                if reason is None:
                    exit_code = proc.returncode
                    status = 'complete' if exit_code == 0 else 'failed'
                    reason = None if exit_code == 0 else f'exit:{exit_code}'
        except Exception as e:
            reason = f'{type(e).__name__}: {e}'
        finally:
            if proc is not None:
                # Kill the entire owned group, even if its leader exited first.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=1)
                exit_code = proc.returncode
                cleaned = not group_members(proc.pid)
                with self.lock:
                    self.active.pop(proc.pid, None)
                    self.cleanup = self.cleanup and cleaned
                    self.max_child_rss = max(self.max_child_rss, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024)
                    if simulation:
                        self.last_sim_end = time.monotonic()
        if not cleaned:
            status, reason = 'failed', 'cleanup_failed'
            self.cancel(reason)
        return {'status':status, 'reason':reason, 'exitCode':exit_code, 'cleanup':cleaned,
                'wallSeconds':time.monotonic()-start, 'command':command}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true', help='Actually sample games; otherwise show plan only')
    p.add_argument('--server', action='store_true', help='Explicit large-run opt-in; never SSHs')
    p.add_argument('--mode', choices=('train', 'smoke'), default='train')
    p.add_argument('--root', type=Path, default=REPO.parent/'Stronghold-Protocol')
    p.add_argument('--entry', type=Path, help='Exact pinned clean-bandbench; local preview-tools or server tools')
    p.add_argument('--out', type=Path, default=HERE/'runs'/'cem')
    p.add_argument('--population', type=int, default=16)
    p.add_argument('--elite', type=int, default=4)
    p.add_argument('--generations', type=int, default=8)
    p.add_argument('--search-seed', type=int, default=314159)
    p.add_argument('--parallel', type=int)
    p.add_argument('--timeout', type=float, default=60, help='Server timeout seconds per natural game')
    p.add_argument('--rss-mib', type=int)
    p.add_argument('--output-mib', type=int)
    a = p.parse_args(argv)
    if not 2 <= a.population <= 64 or not 1 <= a.elite < a.population or not 1 <= a.generations <= 64:
        p.error('Require population 2..64, 1 <= elite < population, generations 1..64')
    if not 0 <= a.search_seed <= 2**32-1 or not 0 < a.timeout <= 3600:
        p.error('Invalid search seed or timeout')
    a.parallel = a.parallel if a.parallel is not None else (4 if a.server else 1)
    if (a.server and not 4 <= a.parallel <= 16) or (not a.server and a.parallel != 1):
        p.error('Server parallel must be 4..16; local parallel must be 1')
    a.rss_mib = a.rss_mib if a.rss_mib is not None else (a.parallel*512+256 if a.server else 512)
    a.output_mib = a.output_mib if a.output_mib is not None else (256 if a.server else 10)
    if not 128 <= a.rss_mib <= 16384 or not 2 <= a.output_mib <= 4096:
        p.error('Invalid resource limits')
    if not a.server and (a.rss_mib > 512 or a.output_mib > 10):
        p.error('Local ceiling: 512 MiB RSS / 10 MiB artifacts')
    if a.server and a.mode == 'smoke':
        p.error('Smoke is a local-only two-game equivalence check')
    a.root, a.out = a.root.resolve(), a.out.resolve()
    a.entry = (a.entry or (a.root/'tools/clean-bandbench.mjs' if a.server else REPO.parent/'preview-tools/clean-bandbench.mjs')).resolve()
    if not a.server and not a.out.is_relative_to(HERE):
        p.error('Local outputs must stay under cem/')
    if a.out == HERE or a.out.is_relative_to(a.root) or (a.out.is_relative_to(REPO) and not a.out.is_relative_to(HERE)):
        p.error('Output must not modify game/policy/other modules')
    return a


class Experiment:
    def __init__(self, args):
        self.a = args
        self.rows = make_plan(args.population, args.generations, args.mode == 'smoke')
        self.manifest = {'format':'cem-v1', 'algorithm':'cross-entropy method',
                         'modelKind':'learned parametric heuristic', 'neuralWeights':False,
                         'searchLearning':{'method':'CEM', 'status':'not_started'},
                         'policyMetadata':{'kind':'controlled upstream bot policy experiment', 'trainedModel':False},
                         'status':'planned', 'config':'solo:ABYSS', 'trainSeeds':TRAIN_SEEDS, 'devSeeds':DEV_SEEDS,
                         'searchSeed':args.search_seed, 'population':args.population, 'elite':args.elite,
                         'generations':args.generations, 'parallelCandidates':args.parallel, 'botbenchJobs':1,
                         'rehearsal':0, 'selection':'lexicographic: complete victories, mean(roundsPassed + actual terminal boss-pool deducted fraction), stable candidate id',
                         'sampling':'independent normalized Gaussian, clipped to [0,1], integer round-half-up; categorical LEVEL_TARGET',
                         'denominator':len(self.rows), 'plannedTrainGames':sum(r['phase']=='train' for r in self.rows),
                         'plannedDevGames':sum(r['phase']=='dev' for r in self.rows),
                         'localSmokeOnly':args.mode == 'smoke', 'args':{k:str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
                         'runtime':{'python':sys.version}, 'games':self.rows}
        self.history, self.best = [], None
        self.lock = threading.RLock()
        self.supervisor = Supervisor(args.out, args.rss_mib, args.output_mib, not args.server)
        self.node = shutil.which('node')
        self.code_hashes = {p.name:digest(p) for p in HERE.iterdir() if p.suffix in ('.py', '.mjs') or p.name == 'pins.json'}
        self.manifest['cemSourceSha256'] = self.code_hashes

    def save(self):
        with self.lock:
            self.manifest['accounting'] = accounting(self.rows)
            atomic_json(self.a.out/'manifest.json', self.manifest)

    def check_code(self):
        if any(digest(HERE/name) != expected for name, expected in self.code_hashes.items()):
            self.supervisor.cancel('cem_source_drift')
            raise ValueError('CEM source drift')

    def guard(self, label):
        self.check_code()
        result = subprocess.run([self.node, '--max-old-space-size=128', str(HERE/'guard.mjs'), str(self.a.root), str(self.a.entry)],
                                env=clean_env(), capture_output=True, timeout=15, check=False)
        if result.returncode:
            raise ValueError('Source guard: '+result.stderr.decode(errors='replace')[:2000])
        value = json.loads(result.stdout)
        if hasattr(self, 'provenance') and value != self.provenance:
            raise ValueError('Source guard changed after startup')
        self.manifest[label] = value
        return value

    def profile(self, name, parameters=None):
        obj = copy.deepcopy(self.provenance['baseProfile'])
        obj['name'] = name
        if parameters is not None:
            obj['overrides'] = parameters
        file = self.a.out/'profiles'/f'{name}.json'
        atomic_json(file, obj)
        return file

    def game(self, row, profile=None, vanilla=False, expected_profile_hash=None):
        if self.supervisor.stop.is_set():
            return
        with self.lock:
            row['status'] = 'running'
            self.save()
        raw = self.a.out/'raw'/f"{row['id']}.json"
        audit = raw.with_suffix('.audit.json')
        env = clean_env()
        env.update(ARK_GAME_ROOT=str(self.a.root), CEM_ENTRY=str(self.a.entry), CEM_AUDIT=str(audit))
        if profile is not None:
            env['ARK_POLICY_PROFILE'] = str(profile)
        if vanilla:
            env['CEM_VANILLA'] = '1'
        command = [self.node, '--max-old-space-size=128', str(HERE/'entry.mjs'), '--configs', 'solo:ABYSS',
                   '--seeds', '1', '--seed', str(row['seed']), '--jobs', '1', '--rehearsal', '0', '--quiet', '--json', str(raw)]
        try:
            self.check_code()
            if profile is not None and digest(profile) != expected_profile_hash:
                raise ValueError('Profile drift before game')
            run = self.supervisor.launch(command, env, raw.with_suffix('.log'), self.a.timeout, simulation=True)
            row.update(run)
            row['raw'] = str(raw.relative_to(self.a.out))
            if run['status'] == 'complete':
                rows = json.loads(raw.read_text())
                if not isinstance(rows, list) or len(rows) != 1:
                    raise ValueError('Expected exactly one raw record')
                row['outcome'] = validate_record(rows[0], row['seed'])
                proof = json.loads(audit.read_text())
                if proof['status'] != 'complete' or not proof.get('sourceUnchangedAfter') or proof['provenance'] != self.provenance:
                    raise ValueError('Missing/changed source audit')
                if proof['trainedModel'] is not False or proof['profileFileSha256'] != expected_profile_hash:
                    raise ValueError('Policy metadata or candidate profile hash mismatch')
                if rows[0]['experiment'].get('sourceBenchmarkSha256') != self.provenance['sources']['botbench']:
                    raise ValueError('Wrong benchmark source in raw record')
                if profile is not None and digest(profile) != expected_profile_hash:
                    raise ValueError('Profile drift after game')
                row['rawSha256'] = digest(raw)
                row['auditSha256'] = digest(audit)
                self.check_code()
        except Exception as e:
            row['status'], row['reason'] = 'failed', str(e)
        finally:
            self.save()

    def evaluate(self, candidate, rows, profile):
        expected = candidate['profileSha256']
        for row in rows:
            if self.supervisor.stop.is_set():
                break
            self.game(row, profile, expected_profile_hash=expected)
            if row['status'] != 'complete':
                break  # remainder is explicitly unrun; candidate cannot be elite
        candidate['fitness'] = fitness(rows)
        candidate['eligible'] = candidate['fitness'] is not None
        candidate['profileSha256'] = expected
        atomic_json(self.a.out/'candidates'/f"{candidate['id']}.json", candidate)

    def train(self):
        cem = CEM(self.provenance['schema'], self.a.search_seed)
        self.manifest['searchLearning']['status'] = 'searching'
        for generation in range(self.a.generations):
            if self.supervisor.stop.is_set():
                raise RuntimeError(self.supervisor.reason)
            self.guard('latestSourceGuard')
            before = cem.state()
            candidates = []
            for i in range(self.a.population):
                name = f'g{generation:02d}-c{i:02d}'
                parameters = cem.sample()
                candidate = {'id':name, 'generation':generation, 'parameters':parameters, 'fitness':None, 'eligible':False}
                candidates.append(candidate)
                candidate['profileSha256'] = digest(self.profile(name, parameters))
                atomic_json(self.a.out/'candidates'/f'{name}.json', candidate)
            # Persist sampled population BEFORE execution so interruption is auditable.
            history = {'generation':generation, 'before':before, 'candidates':candidates, 'status':'running'}
            self.history.append(history)
            atomic_json(self.a.out/'history.json', self.history)
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.a.parallel) as pool:
                futures = [pool.submit(self.evaluate, c, [r for r in self.rows if r['candidate'] == c['id']],
                                       self.a.out/'profiles'/f"{c['id']}.json") for c in candidates]
                for future in futures:
                    future.result()
            self.guard('latestSourceGuard')
            if self.supervisor.stop.is_set():
                raise RuntimeError(self.supervisor.reason)
            elites = select(candidates, self.a.elite)
            cem.update([c['parameters'] for c in elites])
            winner = elites[0]
            if self.best is None or tuple(winner['fitness']) > tuple(self.best['fitness']):
                self.best = copy.deepcopy(winner)
                atomic_json(self.a.out/'best.json', {'frozen':False, **self.best})
            history.update(status='complete', elites=[e['id'] for e in elites], after=cem.state())
            self.manifest['searchLearning']['completedGenerations'] = generation+1
            atomic_json(self.a.out/'history.json', self.history)
        # Only training data determine this file. Never update or choose using dev.
        self.guard('preFreezeGuard')
        frozen = self.profile('frozen-best', self.best['parameters'])
        frozen_hash = digest(frozen)
        self.manifest['frozen'] = {'candidateId':self.best['id'], 'profile':str(frozen.relative_to(self.a.out)),
                                   'sha256':frozen_hash, 'trainFitness':self.best['fitness'], 'time':time.time()}
        self.manifest['searchLearning']['status'] = 'frozen'
        atomic_json(self.a.out/'best.json', {'frozen':True, **self.best})
        self.save()  # freeze is durable before any dev subprocess starts
        default = self.profile('dev-default')
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.a.parallel) as pool:
            futures = [pool.submit(self.game, row, frozen if row['candidate']=='frozen' else default,
                                   expected_profile_hash=frozen_hash if row['candidate']=='frozen' else digest(default))
                       for row in self.rows if row['phase'] == 'dev']
            for future in futures:
                future.result()
        if digest(frozen) != frozen_hash:
            raise ValueError('Frozen profile drift after dev')
        self.manifest['devComparison'] = {arm:accounting([r for r in self.rows if r['phase']=='dev' and r['candidate']==arm])['dev']
                                          for arm in ('frozen', 'default')}

    def smoke(self):
        default = self.profile('smoke-default')
        self.game(self.rows[0], vanilla=True)
        self.game(self.rows[1], default, expected_profile_hash=digest(default))
        if any(r['status'] != 'complete' for r in self.rows):
            raise ValueError('Smoke incomplete; no equivalence or performance conclusion')
        left, right = [json.loads((self.a.out/r['raw']).read_text())[0] for r in self.rows]
        keys = ('config','seed','stageId','bossId','victory','roundsPassed','hidden','lpLeft','players','rounds',
                'bounties','errors','errorMessages','matchErrors','finalDiagnostics','bossRound','experiment')
        same = {k:left.get(k) for k in keys} == {k:right.get(k) for k in keys}
        self.manifest['smokeEquivalence'] = {'equal':same, 'excludedTimingFields':['ms','preps','picks'],
                                            'performanceComparison':False, 'training':False}
        if not same:
            raise ValueError('Default runtime equivalence failed')

    def run(self):
        self.a.out.mkdir(parents=True, exist_ok=False)
        for name in ('raw', 'profiles', 'candidates'):
            (self.a.out/name).mkdir()
        self.save()  # full denominator exists even if initial guard fails
        previous = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda s, _f: self.supervisor.cancel(f'signal:{s}'))
        try:
            if self.node is None:
                raise RuntimeError('node is required; nothing installed automatically')
            self.provenance = self.guard('sourceGuardBefore')
            self.manifest['status'] = 'running'
            self.save()
            if self.a.mode == 'smoke':
                self.smoke()
            else:
                self.train()
            self.manifest['status'] = 'complete' if all(r['status']=='complete' for r in self.rows) else 'complete_with_failures'
        except Exception as e:
            self.manifest['status'], self.manifest['error'] = 'failed', f'{type(e).__name__}: {e}'
            if self.manifest['searchLearning']['status'] == 'searching':
                self.manifest['searchLearning']['status'] = 'failed_unfrozen'
        finally:
            self.supervisor.cancel('finished')
            for row in self.rows:
                if row['status'] == 'running':
                    row['status'], row['reason'] = 'interrupted', 'supervisor_exit'
            # Save history including partial population, not just successful updates.
            for h in self.history:
                if h['status'] == 'running':
                    h.update(status='failed', error=self.manifest.get('error', self.supervisor.reason))
            atomic_json(self.a.out/'history.json', self.history)
            try:
                self.guard('sourceGuardAfter')
            except Exception as e:
                self.manifest['status'], self.manifest['postGuardError'] = 'failed', str(e)
            lingering = [pid for pid in self.supervisor.processes if group_members(pid)]
            elapsed = ((self.supervisor.last_sim_end or time.monotonic())-self.supervisor.started
                       if self.supervisor.started else 0)
            # ru_maxrss for SELF can inherit the launcher's pre-exec high water.
            # /proc VmHWM belongs to this exec's address space, so use it here.
            local_rss_bound = (proc_memory_bytes(os.getpid(), 'VmHWM:') +
                               resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024) if not self.a.server else None
            self.manifest['resources'] = {'peakSampledAggregateRssBytes':self.supervisor.peak_rss,
                'maxReapedChildRssBytes':self.supervisor.max_child_rss, 'simEnvelopeSeconds':elapsed,
                'localAggregateRssUpperBoundBytes':local_rss_bound,
                'cleanupVerified':self.supervisor.cleanup and not lingering, 'liveProcessGroups':lingering,
                'stopReason':self.supervisor.reason, 'rssSamplingSeconds':.02, 'rssHeadroomMiB':64}
            if (lingering or (not self.a.server and elapsed > 8) or
                    self.supervisor.peak_rss > self.a.rss_mib*MIB or self.supervisor.max_child_rss > self.a.rss_mib*MIB):
                self.manifest['status'] = 'failed'
                self.manifest['resourceError'] = 'Cleanup or local time budget exceeded'
            if local_rss_bound is not None and local_rss_bound > self.a.rss_mib*MIB:
                self.manifest['status'], self.manifest['resourceError'] = 'failed', 'Local RSS bound exceeded'
            self.save()
            size = sum(p.stat().st_size for p in self.a.out.rglob('*') if p.is_file())
            self.manifest['resources']['artifactBytesBeforeFinalSave'] = size
            if size > self.a.output_mib*MIB-4096:
                self.manifest['status'], self.manifest['resourceError'] = 'failed', 'Output budget exceeded'
            self.save()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        print(json.dumps({'status':self.manifest['status'], 'out':str(self.a.out), 'accounting':accounting(self.rows)}))
        return 0 if self.manifest['status'] == 'complete' else 1


def main(argv=None):
    a = parse_args(argv)
    experiment = Experiment(a)
    if not a.run:
        plan = {k:v for k,v in experiment.manifest.items() if k not in ('games', 'cemSourceSha256')}
        plan['accounting'] = accounting(experiment.rows)
        plan['executionAllowed'] = a.server or a.mode == 'smoke'
        plan['note'] = 'Dry-run: no files written, no games. Local training refused; explicit --server --run required.'
        print(json.dumps(plan, indent=2))
        return 0
    if not a.server and a.mode != 'smoke':
        raise ValueError('Local training refused: needs >=16 games. Use local --mode smoke (two games), or --server --run on server.')
    return experiment.run()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, FileExistsError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
