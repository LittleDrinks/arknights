#!/usr/bin/env python3
"""Greedy dev-only evaluation, complete raw terminals and auditable failure denominators."""
import argparse
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from common import (DEFAULT_GAME, ROOT, RULES, json_bytes, output_dir, require, sha,
                    source_identity, validate_features)
from dataset import full_terminal
from model import L, load_checkpoint


class LocalEnvironment(L.Environment):
    """Local node only; own process group permits immediate whole-engine cleanup."""
    def __init__(self, game, timeout):
        self.process = subprocess.Popen(['node', '--max-old-space-size=256', str(ROOT / 'rl/env.mjs'),
                                         '--game', str(Path(game).resolve())],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        bufsize=0, start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.timeout = timeout
        self.buffer = bytearray()

    def close(self):
        # No simulated close step or extra timeout after the total wall deadline.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=1)
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()


class EvidenceWriter:
    def __init__(self, out, limit):
        self.out, self.limit, self.used = out, limit - 262144, 0
        require(self.limit > 0, 'Output budget must exceed summary reserve')

    def append(self, path, row):
        raw = json_bytes(row)
        require(self.used + len(raw) <= self.limit, 'Output budget exhausted; evidence incomplete')
        with open(path, 'ab') as stream:
            stream.write(raw)
        self.used += len(raw)

    def report(self, report):
        raw = json_bytes(report)
        require(len(raw) <= 262144, 'Summary reserve exhausted')
        (self.out / 'eval.json').write_bytes(raw)


def evaluate(checkpoints, out, *, seeds=range(1, 17), difficulties=('NORMAL', 'ABYSS'),
             game=DEFAULT_GAME, budget=128, wall_seconds=9, max_output_bytes=9_000_000):
    require(0 < wall_seconds and wall_seconds < 86400, 'Finite evaluation wall budget required')
    require(type(budget) is int and 1 <= budget <= 1024, 'Invalid prep budget')
    require(all(type(seed) is int and 1 <= seed <= 16 for seed in seeds) and len(set(seeds)) == len(seeds),
            'This CLI evaluates dev seeds 1..16 only; never validation/test')
    require(bool(seeds) and difficulties and len(set(difficulties)) == len(difficulties) and
            all(d in ('NORMAL', 'ABYSS') for d in difficulties), 'NORMAL/ABYSS only')
    require(0 < len(checkpoints) <= 4, 'One to four checkpoint files')
    writer = EvidenceWriter(out, max_output_bytes)
    source = source_identity()
    rows = [{'checkpointIndex': i, 'seed': seed, 'difficulty': d, 'status': 'not-run', 'victory': False}
            for i in range(len(checkpoints)) for d in difficulties for seed in seeds]
    report = {'kind': 'interaction-dev-eval/v1', 'source': source, 'split': 'dev', 'budget': budget,
              'wallSecondsLimit': wall_seconds, 'maxOutputBytes': max_output_bytes,
              'checkpoints': [{'path': str(Path(p).resolve())} for p in checkpoints],
              'planned': len(rows), 'episodes': rows, 'status': 'running',
              'warning': 'Dev diagnostics only; no validation/test claim. Synthetic weights do not establish performance.'}
    writer.report(report)  # Planned denominator is on disk before loading any checkpoint or launching engine.
    start = time.monotonic()
    deadline = start + wall_seconds
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise TimeoutError('Total evaluation wall budget exhausted')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, wall_seconds)
    exhausted = False
    try:
        for index, path in enumerate(checkpoints):
            if exhausted or time.monotonic() >= deadline:
                break
            selected = [r for r in rows if r['checkpointIndex'] == index]
            try:
                checkpoint_sha = sha(path)
                report['checkpoints'][index]['checkpointSHA256'] = checkpoint_sha
                model, ckpt = load_checkpoint(path)
                require(sha(path) == checkpoint_sha, 'Checkpoint changed during load')
                metadata = ckpt['metadata']
                train_episodes = metadata.get('dataset', {}).get('manifest', {}).get('episodes', [])
                report['checkpoints'][index].update(architecture=ckpt['architecture'],
                    syntheticOnly=metadata.get('syntheticOnly', False), trainingSource=metadata.get('source'),
                    datasetSHA256=metadata.get('dataset', {}).get('datasetSHA256'))
            except Exception as error:
                for row in selected:
                    row.update(status='failed', error=str(error)[:2000])
                writer.report(report)
                if time.monotonic() >= deadline:
                    exhausted = True
                continue
            for row in selected:
                if time.monotonic() >= deadline or exhausted:
                    exhausted = True
                    break
                env = None
                prefix = f"checkpoint-{index}-{row['difficulty']}-{row['seed']}"
                trace_path = out / (prefix + '.trace.jsonl')
                terminal_path = out / (prefix + '.terminal.json')
                row.update(status='running', checkpointSHA256=checkpoint_sha, sourceSHA256=source['sourceSHA256'],
                           architecture=ckpt['architecture']['name'], trace=trace_path.name,
                           trainEpisodeOverlap=any(e['seed'] == row['seed'] and e['difficulty'] == row['difficulty']
                                                   for e in train_episodes))
                writer.report(report)
                try:
                    writer.append(trace_path, {'type': 'identity', 'checkpointSHA256': checkpoint_sha,
                                               'source': source, 'episode': row.copy(), 'prepBudget': budget})
                    env = LocalEnvironment(game, max(.001, deadline - time.monotonic()))
                    state = env.request('reset', seed=row['seed'], difficulty=row['difficulty'], mode='solo', budget=budget)
                    expected = {'seed': row['seed'], 'difficulty': row['difficulty'], 'mode': 'solo', 'budget': budget}
                    steps = 0
                    while True:
                        require(state['schema'] == ckpt['schema'] and state['rules'] == RULES and
                                state['episode'] == expected, 'Live schema/rules/episode mismatch')
                        if state['done']:
                            # Save the unabridged raw terminal even if admission subsequently fails.
                            writer.append(terminal_path, state)
                            row.update(terminal=terminal_path.name, terminalSHA256=sha(terminal_path))
                            full_terminal(state, expected, ckpt['schema'])
                            require(state['steps'] == steps, 'Trace/terminal step mismatch')
                            writer.append(trace_path, {'type': 'terminal', 'terminal': terminal_path.name,
                                                       'terminalSHA256': row['terminalSHA256'], 'policySteps': steps})
                            row.update(status='complete', victory=state['victory'], result=L.episode_result(state),
                                       traceSHA256=sha(trace_path))
                            break
                        validate_features(state['features'], model.dimensions)
                        require(len(state['candidates']) == len(state['features']['candidates']), 'Live candidate alignment')
                        action, _, _ = model.act(state['features'], greedy=True)
                        require(state['features']['mask'][action], 'Policy selected masked action')
                        writer.append(trace_path, {'type': 'decision', 'step': steps, 'round': state['rounds'],
                                                  'features': state['features'], 'candidates': state['candidates'],
                                                  'candidateIndex': action, 'action': state['candidates'][action]})
                        state = env.request('step', candidateIndex=action)
                        steps += 1
                except Exception as error:
                    row.update(status='failed', victory=False, error=str(error)[:2000])
                    if trace_path.exists():
                        row['partialTraceSHA256'] = sha(trace_path)
                    exhausted = time.monotonic() >= deadline or 'Output budget' in str(error)
                finally:
                    if env is not None:
                        env.close()
                writer.report(report)
    except Exception as error:
        report['error'] = str(error)[:2000]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        for row in rows:
            if row['status'] == 'running':
                row.update(status='failed', victory=False, error='Interrupted before evidence completion')
            elif row['status'] == 'not-run':
                row['error'] = 'Plan not executed within budget or after failure'
        groups = []
        for index in range(len(checkpoints)):
            for difficulty in difficulties:
                group = [r for r in rows if r['checkpointIndex'] == index and r['difficulty'] == difficulty]
                wins = sum(r['status'] == 'complete' and r['victory'] for r in group)
                groups.append({'checkpointIndex': index, 'difficulty': difficulty, 'planned': len(group),
                               'completed': sum(r['status'] == 'complete' for r in group),
                               'failed': sum(r['status'] == 'failed' for r in group),
                               'notRun': sum(r['status'] == 'not-run' for r in group),
                               'wins': wins, 'winsOverPlanned': wins / len(group)})
        report.update(groups=groups, seconds=time.monotonic() - start,
                      status='complete' if all(r['status'] == 'complete' for r in rows) else 'incomplete',
                      evidenceBytes=writer.used)
        writer.report(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', action='append', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--game', default=str(DEFAULT_GAME))
    parser.add_argument('--seeds', default=','.join(map(str, range(1, 17))))
    parser.add_argument('--difficulties', default='NORMAL,ABYSS')
    parser.add_argument('--budget', type=int, default=128)
    parser.add_argument('--wall-seconds', type=float, default=9)
    parser.add_argument('--max-output-bytes', type=int, default=9_000_000)
    args = parser.parse_args()
    report = evaluate(args.checkpoint, output_dir(args.out), seeds=[int(s) for s in args.seeds.split(',')],
                      difficulties=tuple(args.difficulties.split(',')), game=args.game, budget=args.budget,
                      wall_seconds=args.wall_seconds, max_output_bytes=args.max_output_bytes)
    print(json_bytes({'status': report['status'], 'planned': report['planned'], 'groups': report['groups']}).decode(), end='')
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
