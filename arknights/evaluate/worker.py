"""One trusted strategy, one Session, one natural terminal; no automatic actions."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import signal
import sys
import traceback
from pathlib import Path

from . import process
from .runner import (_json, _safe_path, _write_json, check_identity, encoded, learner_victory,
                     team_victory, validate_case)


def perform(spec, case, script, root, directory):
    """Internal seam permits fixture Session substitution without changing policy API."""
    from arknights.adapter import Session

    result = {'id': case['id'], 'status': 'failed'}
    game = None
    try:
        if str(script) != spec['script'] or str(root) != spec['root']:
            raise ValueError('Worker arguments differ from immutable specification')
        if directory.name != case['id'] or directory.parent.name != spec['run']:
            raise ValueError('Worker case directory differs from plan')
        check_identity(root, spec['identity'])
        module_spec = importlib.util.spec_from_file_location(
            '_arknights_policy_' + spec['identity']['script']['sha256'][:16], script)
        if module_spec is None or module_spec.loader is None:
            raise ValueError('Cannot load strategy script')
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        sys.path.insert(0, str(script.parent))
        source = script.read_bytes()
        if hashlib.sha256(source).hexdigest() != spec['identity']['script']['sha256']:
            raise ValueError('Strategy source changed before import')
        # SourceFileLoader may prefer a timestamp-valid .pyc even with -B. Compile
        # the captured bytes explicitly so provenance describes executed policy.
        exec(compile(source, str(script), 'exec'), module.__dict__)
        play = getattr(module, 'play', None)
        if not callable(play):
            raise ValueError('Strategy must define play(game)')
        # Session creates trajectory exclusively and performs reset itself.
        with Session(root=root, directory=directory / 'trajectory', timeout=spec['timeout'],
                     max_requests=spec['max_requests'], seed=case['seed'], mode=spec['mode'],
                     difficulty=spec['difficulty'], partners=case['partner']) as game:
            returned = play(game)
            if inspect.isawaitable(returned):
                if inspect.iscoroutine(returned):
                    returned.close()
                raise TypeError('play(game) must be synchronous')
            state = game.observe()
            victory = team_victory(state)
            result.update(status='complete', state=state, teamVictory=victory,
                          learnerIndividualVictory=learner_victory(state),
                          stateSHA256=hashlib.sha256(encoded(state)).hexdigest())
        # These checks happen after close/EOF has finished recording the pipes.
        from .runner import _json_lines
        result['requests'] = sum(1 for _ in _json_lines(directory / 'trajectory/requests.jsonl'))
        check_identity(root, spec['identity'])
        validate_case(directory, case, spec, result=result)
        _write_json(directory / 'result.json', result, exclusive=True)
        return 0
    except BaseException as exc:
        result.update(status='failed', teamVictory=None, learnerIndividualVictory=None,
                      error=f'{type(exc).__name__}: {exc}')
        if game is not None:
            result['prefixState'] = getattr(game, 'state', None)
        # Preserve an existing artifact even if publication itself failed.
        name = 'failure.json' if (directory / 'result.json').exists() else 'result.json'
        _write_json(directory / name, result, exclusive=True)
        traceback.print_exc()
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('spec', 'case', 'script', 'root', 'case-dir'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    process._IN_WORKER = True
    # Allow an active Session to close cleanly on supervisor cancellation. SIGKILL
    # of the enclosing group still covers import loops and uncooperative code.
    def terminate(_signum, _frame):
        raise SystemExit('Evaluation worker cancelled')
    signal.signal(signal.SIGTERM, terminate)
    root, script, directory = map(_safe_path, (args.root, args.script, args.case_dir))
    spec = _json(args.spec)
    cases = [case for case in spec['cases'] if case['id'] == args.case]
    if len(cases) != 1:
        parser.error('Case is not unique in the immutable plan')
    return perform(spec, cases[0], script, root, directory)


if __name__ == '__main__':
    raise SystemExit(main())
