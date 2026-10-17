"""Fixed development-only 128-game schedule; raw protocol evidence and bounded processes."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
import selectors
import signal
import subprocess
import time

from common import (ARMS, CONFIG, LIMITATIONS, RULES, TEAMS, clean_env, digest, dimensions, encoded, game_identity,
                    features, parse, require, sha, source_identity, terminal_contract, write)


def schedule():
    return [{'id': f'{model}-{pool}-{seed:02}', 'model': model, 'pool': pool, 'seed': seed,
             'mode': 'coop', 'difficulty': 'ABYSS', 'seat': 0, 'status': 'unrun',
             'steps': None, 'eof': False, 'error': None, 'engineErrors': None,
             'actionErrors': None, 'controllerErrors': None, 'controllerDiagnosticErrors': None,
             'teamVictory': None, 'learnerIndividualVictory': None}
            for model in ('parent', *ARMS) for pool in TEAMS for seed in range(1, 17)]


def summary(rows):
    require(len(rows) == 128 and [r['id'] for r in rows] == [r['id'] for r in schedule()], 'Fixed denominator/schedule')
    def count(rs):
        n = len(rs)
        team = sum(r['status'] == 'complete' and r['teamVictory'] is True for r in rs)
        individual = sum(r['status'] == 'complete' and r['learnerIndividualVictory'] is True for r in rs)
        return {'denominator': n, 'complete': sum(r['status'] == 'complete' for r in rs),
                'failed': sum(r['status'] == 'failed' for r in rs), 'unrun': sum(r['status'] == 'unrun' for r in rs),
                'teamWins': team, 'learnerWins': individual, 'teamWinsPerScheduled': team / n,
                'learnerWinsPerScheduled': individual / n}
    return {**count(rows), 'cells': {f'{m}/{p}': count([r for r in rows if r['model'] == m and r['pool'] == p])
                                  for m in ('parent', *ARMS) for p in TEAMS}}


class Protocol:
    """Drain stdout AND stderr; absolute game deadline, byte cap, close ack AND EOF."""
    def __init__(self, command, env, out, deadline, byte_limit):
        self.deadline, self.limit, self.bytes = deadline, byte_limit, 0
        self.buffer = bytearray()
        self.raw = (out / 'protocol.jsonl').open('xb')
        self.errors = (out / 'stderr.log').open('xb')
        self.actions = (out / 'actions.jsonl').open('xb')
        self.selector = selectors.DefaultSelector()
        self.process = None
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=0, env=env, start_new_session=True)
            self.selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
            self.selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')
        except BaseException:
            self.close()
            raise
        self.stderr_bytes = 0

    def charge(self, n):
        self.bytes += n
        require(self.bytes <= self.limit, 'Per-game output budget exceeded')

    def pump(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Absolute game/run timeout')
        events = self.selector.select(remaining)
        if not events:
            raise TimeoutError('Environment response timeout')
        for key, _ in events:
            chunk = os.read(key.fd, 65536)
            if not chunk:
                self.selector.unregister(key.fileobj)
                continue
            self.charge(len(chunk))
            if key.data == 'stdout':
                self.raw.write(chunk)  # Verbatim bytes, including malformed/failing responses.
                self.buffer.extend(chunk)
                require(len(self.buffer) <= 20_000_001, 'Protocol line budget')
            else:
                self.errors.write(chunk)
                self.stderr_bytes += len(chunk)

    def request(self, command):
        require(time.monotonic() < self.deadline, 'Absolute game/run deadline')
        self.process.stdin.write(encoded(command))
        while b'\n' not in self.buffer:
            require(any(k.data == 'stdout' for k in self.selector.get_map().values()), 'Unexpected protocol EOF')
            self.pump()
        line, _, rest = self.buffer.partition(b'\n')
        self.buffer = bytearray(rest)
        return parse(line), bytes(line) + b'\n'

    def action(self, record):
        data = encoded(record)
        self.charge(len(data))
        self.actions.write(data)

    def finish(self):
        ack, _ = self.request({'cmd': 'close'})
        require(ack == {'ok': True}, 'Close acknowledgement')
        self.process.stdin.close()
        while self.selector.get_map():
            self.pump()
        require(not self.buffer, 'Extra/truncated data after close acknowledgement')
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'EOF deadline')
        require(self.process.wait(timeout=remaining) == 0 and self.stderr_bytes == 0, 'Engine exit/stderr error')

    def close(self):
        if self.process:
            # Always clear the private process group, including after a malformed response.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=2)
            for f in (self.process.stdin, self.process.stdout, self.process.stderr):
                if f and not f.closed:
                    f.close()
        self.selector.close()
        for f in (self.raw, self.errors, self.actions):
            f.close()


def state_contract(state, row, ckpt, steps, pool_manifest):
    require(state['ok'] is True, 'Engine rejected request: ' + str(state.get('error', 'unknown'))[:2000])
    require(type(state['done']) is bool and state['schema'] == ckpt['schema'] and state['rules'] == RULES, 'State schema/rules')
    require(type(state['steps']) is int and state['steps'] == steps, 'State step continuity')
    require(type(state['engineErrors']) is int and state['engineErrors'] == 0 and
            type(state['actionErrors']) is int and state['actionErrors'] == 0 and
            state.get('fatalRejections', 0) == 0, 'Engine/action/fatal error')
    e = state['episode']
    require(e['seed'] == row['seed'] and e['mode'] == 'coop' and e['difficulty'] == 'ABYSS' and e['budget'] == 128,
            'State seed/mode/difficulty/budget')
    p = e['partners']
    team = TEAMS[row['pool']]
    require(p['pool'] == ('normal' if row['pool'] == 'original' else 'mixed') and p['split'] == 'eval' and
            p['poolSeed'] == 313 and p['fixed'] == team and p['manifest'] == 'coop-heuristics-v1' and
            p['manifestSHA256'] == pool_manifest, 'Frozen pool identity')
    families = {'original-v1': 'upstream-bot', 'buy2-v1': 'first-offer-buy2', 'saver-v1': 'level-first-saver'}
    require(p['seats'] == [{'seat': i + 1, 'id': ident, 'family': families[ident]} for i, ident in enumerate(team)], 'Pool seats')
    obs = state['observation']
    require(obs['own']['playerId'] == 'learner' and obs['own']['seat'] == 0, 'Learner seat')
    features(state['features'], dimensions(ckpt['schema']), terminal=state['done'])
    cs = state['candidates']
    require(len(cs) == len(state['features']['candidates']) and
            all(c['type'] in ('buy', 'sell', 'refresh', 'levelUp', 'ready') for c in cs), 'Candidate/action contract')
    if not state['done']:
        require(state['summary'] is None and 'diagnostics' not in e and 'partnerAudit' not in state and
                state['reward'] == 0 and state['victory'] is False, 'Terminal information before terminal')


def game_job(row, model, ckpt, code, game, out, deadline, byte_limit, pool_manifest):
    result = dict(row)
    directory = out / row['id']
    directory.mkdir(exist_ok=False)
    channel, steps, started = None, 0, time.monotonic()
    try:
        env = clean_env()
        env.update(COOP_POOL='normal' if row['pool'] == 'original' else 'mixed', COOP_SPLIT='eval',
                   COOP_POOL_SEED='313', COOP_TEAM=','.join(TEAMS[row['pool']]))
        channel = Protocol(['node', str(code / 'coop/env.mjs'), '--game', str(game)], env, directory, deadline, byte_limit)
        command = {'cmd': 'reset', 'seed': row['seed'], 'mode': 'coop', 'difficulty': 'ABYSS', 'budget': 128, 'teacher': False}
        while True:
            state, raw = channel.request(command)
            if state.get('done') is True:
                channel.charge(len(raw))
                with (directory / 'terminal.json').open('xb') as f:
                    f.write(raw)  # Full response, unmodified, even if later validation fails.
            state_contract(state, row, ckpt, steps, pool_manifest)
            if state['done']:
                individual = terminal_contract(state, seed=row['seed'], steps=steps, schema=ckpt['schema'])
                channel.finish()
                result.update(status='complete', teamVictory=state['summary']['victory'],
                              learnerIndividualVictory=individual['victory'], learnerOutcome=individual,
                              rounds=state['rounds'], eof=True, engineErrors=state['engineErrors'],
                              actionErrors=state['actionErrors'], controllerErrors=state['controllerErrors'],
                              controllerDiagnosticErrors=state['controllerDiagnosticErrors'],
                              partnerActionErrors=state['partnerActionErrors'])
                break
            require(steps < 4096, 'Maximum policy steps exceeded; not a terminal result')
            # The model only receives the original, current feature object.
            action, _, _ = model.act(state['features'], greedy=True)
            require(type(action) is int and 0 <= action < len(state['candidates']) and state['features']['mask'][action], 'Illegal greedy action')
            channel.action({'step': steps, 'round': state['rounds'], 'seat': 0, 'candidates': state['candidates'],
                            'mask': state['features']['mask'], 'candidateIndex': action, 'action': state['candidates'][action]})
            command = {'cmd': 'step', 'candidateIndex': action}
            steps += 1
    except Exception as error:
        result.update(status='failed', error=f'{type(error).__name__}: {error}'[:4000], eof=False)
    finally:
        if channel:
            channel.close()
        result.update(steps=steps, seconds=time.monotonic() - started,
                      outputBytes=channel.bytes if channel else 0)
        result['files'] = {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(directory.iterdir()) if p.is_file()}
        write(directory / 'result.json', result)
    return result


def evaluate(L, trained, parent, code, game_path, game, out, source, *, workers, timeout, total_timeout, byte_limit):
    require(type(workers) is int and 1 <= workers <= 4 and 0 < timeout <= 600 and
            0 < total_timeout <= 21600 and 0 < byte_limit <= 64 * 1024 * 1024, 'Evaluation resource bounds')
    train_path = trained / 'train.json'
    report = parse(train_path.read_bytes())
    require(report['status'] == 'complete' and report['config'] == CONFIG and report['source'] == source and
            report['game'] == game and report['configSHA256'] == digest(encoded(CONFIG)), 'Completed train/source/config required')
    require(sha(trained / 'input-metadata.json') == report['metadataSHA256'] and
            sha(trained / 'selection.json') == report['selectionSHA256'], 'Training evidence changed')
    require(sha(parent) == report['parent']['sha256'], 'Parent changed')
    require(set(report['arms']) == set(ARMS), 'Three trained arms required')
    models, hashes = {}, {'train.json': sha(train_path), 'parent': sha(parent)}
    for name in ('parent', *ARMS):
        path = parent if name == 'parent' else trained / (name + '.pt')
        if name != 'parent':
            arm = report['arms'][name]
            require(sha(path) == arm['checkpointSHA256'] and arm['actualUpdates'] == 200 and arm['actualDraws'] == 6800,
                    'Arm checkpoint/budget mismatch')
        model, ckpt = L.load_checkpoint(path)
        require(ckpt['schema'] == game['schema'] and ckpt['rules'] == RULES and
                ckpt['dimensions'][:2] == dimensions(game['schema']), 'Eval checkpoint schema/rules')
        expected = report['parent']['parameterSHA256'] if name == 'parent' else report['arms'][name]['parameterSHA256']
        require(L.digest(model) == expected, 'Model parameter SHA mismatch')
        require(all(L.torch.isfinite(v).all().item() for v in model.state_dict().values()), 'Nonfinite eval model')
        if name != 'parent':
            md = ckpt['metadata']
            require(md['arm'] == name and md['source'] == source and md['parent'] == report['parent'] and
                    md['config'] == CONFIG and md['rawInputSHA256'] == report['rawInputSHA256'], 'Checkpoint provenance mismatch')
        model.eval()
        model.requires_grad_(False)
        models[name] = (model, ckpt)
        hashes[name] = sha(path)
    # JS JSON.stringify(pool.json) is compact, insertion-ordered, with integer/string fields.
    import json
    pool_manifest = digest(json.dumps(parse((code / 'coop/pool.json').read_bytes()), separators=(',', ':')).encode())
    rows = schedule()
    manifest = {'kind': 'dev-only-survivor-eval/v1', 'denominator': 128, 'games': rows,
                'models': hashes, 'source': source, 'game': game, 'limitations': LIMITATIONS,
                'inputReviewStatus': report['inputReviewStatus'], 'greedy': True, 'cpuWorkers': workers,
                'timeoutSeconds': timeout, 'totalTimeoutSeconds': total_timeout, 'maxSteps': 4096,
                'outputBudgetPerGame': byte_limit, 'maxProtocolBytesTotal': 128 * byte_limit,
                'poolSeed': 313, 'split': 'eval', 'teams': TEAMS,
                'rawInputSHA256': report['rawInputSHA256'], 'trainConfigSHA256': report['configSHA256']}
    manifest['configSHA256'] = digest(encoded({k: manifest[k] for k in (
        'greedy', 'cpuWorkers', 'timeoutSeconds', 'totalTimeoutSeconds', 'maxSteps',
        'outputBudgetPerGame', 'poolSeed', 'split', 'teams', 'games')}))
    write(out / 'manifest.json', manifest)  # Immutable schedule BEFORE any environment starts.
    deadline = time.monotonic() + total_timeout
    error = None
    write(out / 'summary.json', {'status': 'running', **summary(rows), 'games': rows})
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending, next_index = {}, 0
            while pending or next_index < 128:
                while len(pending) < workers and next_index < 128 and time.monotonic() < deadline:
                    i = next_index
                    row = rows[i]
                    future = executor.submit(game_job, row, *models[row['model']], code, game_path, out,
                                             min(deadline, time.monotonic() + timeout), byte_limit, pool_manifest)
                    pending[future] = i
                    next_index += 1
                if not pending:
                    break
                completed, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in completed:
                    i = pending.pop(future)
                    try:
                        rows[i] = future.result()
                    except Exception as exc:
                        rows[i] = {**rows[i], 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:4000]}
                write(out / 'summary.json', {'status': 'running', **summary(rows), 'games': rows})
        require(source_identity(code) == source and sha(train_path) == hashes['train.json'], 'Source/train drift during eval')
        require(game_identity(game_path.parent, code)[1] == game, 'Game source/data drift during eval')
        for name, (model, _) in models.items():
            path = parent if name == 'parent' else trained / (name + '.pt')
            require(sha(path) == hashes[name], 'Checkpoint changed during evaluation')
            expected = report['parent']['parameterSHA256'] if name == 'parent' else report['arms'][name]['parameterSHA256']
            require(L.digest(model) == expected, 'Frozen model mutated')
    except BaseException as exc:
        error = f'{type(exc).__name__}: {exc}'[:4000]
    result = {'status': 'complete' if not error and all(r['status'] == 'complete' for r in rows) else 'failed',
              'error': error, **summary(rows), 'games': rows}
    if error:
        # No successful result may be credited under source/model drift.
        for row in rows:
            if row['status'] == 'complete':
                row.update(status='failed', error='Run invalidated: ' + error)
        result.update(summary(rows))
    write(out / 'summary.json', result)
    return result


def run_evaluation(L, trained, parent, code, game_path, game, out, source, **limits):
    """Even a rejected posttrain artifact leaves the fixed unrun denominator on disk."""
    try:
        return evaluate(L, trained, parent, code, game_path, game, out, source, **limits)
    except Exception as error:
        rows = schedule()
        result = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'[:4000], **summary(rows), 'games': rows}
        if not (out / 'manifest.json').exists():
            write(out / 'manifest.json', {'kind': 'dev-only-survivor-eval/v1', 'denominator': 128,
                                         'games': rows, 'admissionFailed': True})
        write(out / 'summary.json', result)
        return result
