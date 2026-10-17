"""Regression for corrupt raw terminals. No Match or Torch import."""
import copy
import json
from pathlib import Path
from dataset import full_terminal

original = json.loads((Path(__file__).parent / 'evidence/checkpoint-0-NORMAL-1.terminal.json').read_text())
expected, schema = original['episode'], original['schema']
full_terminal(original, expected, schema)
faults = [(['summary', 'errors'], v) for v in (1, -1, None, False)]
faults += [(['summary', 'players', 0, 'victory'], True),
           (['summary', 'players', 0, 'roundsPassed'], 99),
           (['episode', 'seed'], True), (['episode', 'budget'], 128.0),
           (['summary', 'seed'], True), (['summary', 'roundsPassed'], 3.0)]
for path, value in faults:
    terminal = copy.deepcopy(original)
    target = terminal
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    try:
        full_terminal(terminal, expected, schema)
    except ValueError:
        pass
    else:
        raise AssertionError((path, value))
terminal = copy.deepcopy(original)
del terminal['summary']['errors']
try:
    full_terminal(terminal, expected, schema)
except ValueError:
    pass
else:
    raise AssertionError('Missing summary.errors accepted')
print('PASS: real raw terminal admitted; eleven corrupt error/outcome/identity cases rejected')
