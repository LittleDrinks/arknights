# Synthetic validation only

Base: `d8805de9ccc1d4d861fe4a60f67197442078e6d9`.

Command: `python3 -B natural-win-bc/check.py`.

Final run: **7 tests passed**, supervisor wall time **2.574s**, aggregate child peak RSS
**710.33 MiB**, peak temporary output **510005 bytes**. An earlier passing run took 2.530s;
the final run followed stricter teacher error admission and per-group draw accounting changes.
Both runs together were below 20s. Temporary artifacts were removed by the supervisor.

The tests exercised synthetic 2048-row manifests with complete/failed/not-run rows, same-seed
normal/mixed positives, a dead-primary team carry, full terminal/row checks, bad hashes,
truncated gzip/trailing bytes/multiple JSON/duplicate keys/nonfinite values, rejected and
illegal labels, source guards, output refusal/caps, the original 200-update BC path and
checkpoint roundtrip, exact draw order and unchanged critic head, a mocked 64-game scheduler,
and the unchanged game_job retaining a synthetic terminal when its EOF check fails.

The syntax check used Python 3.10 grammar; execution used the installed Python 3.12 interpreter
and existing torch. Python 3.10 runtime compatibility was not separately executed.
The test harness substitutes synthetic protocol objects, never starts the real game loader,
and never constructs Environment, Match or Battle. Actual protocol/engine behavior remains
the responsibility of the pinned, previously reviewed game_job and main's later server run.

No SSH, installation, subagent, goal, production download, production training or real game
was performed. No results for the currently collecting teacher batch are claimed here.
Collector source trust remains pending main's independent acceptance. Eval supports only
original/mixed, 64 scheduled dev games; no heldout families and no sealed seeds.
