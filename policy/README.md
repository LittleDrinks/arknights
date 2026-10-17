# Controlled bot policy experiments

Only the pinned `server/match/bot.js` decision code is transformed in memory. Game data, simulation, money, layers, shop offers and enemy rules stay intact. Profiles are experimental hypotheses, not trained models or proven guide reproductions.

Node 22 is sufficient; no packages are installed. Generate/check profiles with:

```sh
node policy/generate-profiles.mjs
node policy/test.mjs /path/to/game
node policy/run.mjs --game /path/to/game --profile policy/profiles/early-level.json --out /path/to/new-run/early.json --configs solo:ABYSS,coop4:ABYSS --seeds 16 --seed 1 --jobs 4 --rehearsal 0 --quiet
```

`--entry` optionally selects an existing benchmark wrapper, such as the main project's `clean-bandbench.mjs`, to add terminal diagnostics. The original benchmark and bot content hashes are still checked. The child inherits the module registration across worker threads. Omitting `--profile` or using `default.json` is a byte-identical no-op. Existing outputs are refused; manifests/logs are adjacent to the requested JSON. The manifest stores profile values, original/transformed source hashes, command and actual exit status. Never count an unfinished or erroneous batch as successful.

The twelve profiles cover early/slow/low shop-level targets, bench pairs, refresh budget, full-board buying, merge scores, focus membership and removal of layer/economy scoring priors. `low-level-target` is a preference; upstream spare-money/free upgrades can exceed it. Removing a scoring prior does not disable the units' actual layer/economy effects. Some profiles change several linked values and are named combinations; use single-change arms for causal attribution before interpreting combinations.

`MAX_REFRESHES` is a bot decision budget, not a rule cap. All actions still pass the original engine validation. This runner applies a profile to every bot seat; it does not yet implement heterogeneous teammate assignments. Actor learning, BC, PPO and partner modelling are separate modules and have not been trained by this tool.
