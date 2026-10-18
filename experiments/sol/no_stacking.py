"""Same policy with deliberate engine preference, placement and cycling disabled."""
from experiments.sol.agent import Policy


def play(game):
    Policy(game, stacking=False).run()
