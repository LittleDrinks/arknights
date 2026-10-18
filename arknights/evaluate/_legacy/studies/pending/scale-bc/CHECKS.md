# Synthetic validation and server handoff

Base: `04928de6615d7ebd7c53351971faf3657524c981`. Only `scale-bc/` is added.

Command (2026-10-18):

```text
/home/q2635/venvs/logics-v3/bin/python -B scale-bc/check.py

Ran 16 tests in 9.164s
OK
Synthetic only: seconds=9.741; peakRSSMiB=638.70; peakOutputBytes=1447390
```

The test supervisor limits the complete subtree to 19 seconds before kill,
1 GiB sampled aggregate RSS and 5,000,000 temporary output bytes. Temporary
fixtures are deleted. No real corpus was read. No real training, game, static
game loader, SSH, download, installation, subagent or goal was used.

Coverage:

- The exact 8192 plan with four tiny fabricated completed cases and 8188 not-run
  positions, all 32 lane journals, real independent reconciliation and actual
  `A.complete_contract`. Mixed/normal positive labels share seed groups correctly.
- Running, unfrozen, globally failed, errored, false complete, altered seed plan,
  failed/interrupted/unknown statuses and missing raw/completed evidence reject.
  An all-complete 8192 header without all corresponding journals also rejects.
- Death-to-positive forgery with internally consistent raw hashes; invalid full
  terminal diagnostics or forbidden private feature fields on **negative** rows;
  wrong bound manifest/source/game identity; unexpected files reject.
- Gzip trailer/CRC/EOF, trailing garbage, missing newline and multiple JSON
  payloads; symlink ancestors, dangling links, FIFO, path traversal, output
  overlap/collision/replacement and byte-budget exhaustion reject.
- Direct original `A.admit` on a synthetic old 2048 plan with six/498 selected
  labels; exact historical-order membership/reordering and mismatch rejection.
- Receipt bound to the new source, game, both manifests, historical report and
  parent. Invalid receipt assertions/bindings reject. This is an attestation
  contract, not authentication of a reviewer.
- Original tiny `L.ActorCritic(2,2,2)` through production `original_train` and
  original F/T/L: one full 15-epoch, 3000-step, 102000-access update. Two shorter
  original one-epoch runs verify independent fresh Adam, deterministic exact
  parameters and the first epoch's agreement with the full 15-epoch order.
  All eight parameters roundtrip through standard checkpoints; initial parent
  and critic head stay fixed. Original 6800-entry cyclic draws retain all 6800
  labels and all 3400 paired groups; overflow/metadata-bearing samples reject.
- Actual admission-only CLI execution on synthetic new evidence, substituting
  external historical assets and static game identity. Successful final guards
  publish complete; final game drift publishes failed. Neither path calls the
  learner or emits a checkpoint. Python 3.10 AST and finite phase timeout checked.

An earlier 15-test run also executed **two full** synthetic 3000-step updates
and confirmed all eight parameter hashes identical: 18.828 seconds, 705.42 MiB,
1,392,747 temporary bytes. The retained suite uses one full update and two short
deterministic comparisons to leave margin below the 19-second kill threshold.

Required server checks, **not executed here**:

1. Independently accept the finished teacher source and corpus; create a bound
   source-acceptance receipt and supply both full CLI SHA arguments. Running or
   unfrozen source remains inadmissible. Receipt format and commands: README.md.
2. Verify the existing original parent, old natural manifest/raw corpus and
   frozen F/T reference report/checkpoint/metadata/draw plan at their pinned
   hashes. The old corpus is consumed by the unchanged original admission.
3. Run admission-only: full 8192 raw semantic audit plus full old admission,
   complete directory inventories and game486/source/static-data guards. The
   real 1800-second audit budget, 8 GiB per-process address-space limit and
   256 MiB default output budget have not been performance-validated.
4. Run the requested train arms in a fresh output directory. Require final
   status complete, all guards and actual budgets, unchanged critic head,
   standard roundtrip, and old6Reference's eight-parameter equality to af5f6ab…
   when that arm is requested. File hashes may differ due to metadata.
5. Main may subsequently approve evaluation using existing `P.game_job`; there
   is no evaluation CLI, no automatic evaluation and no win-rate claim here.

Required retained output: final admission/train report, before/after source
evidence, bound receipt, frozen manifests, audit metadata and inventories,
reference/actual draw plans, actual accesses, and requested standard checkpoints.
Input corpora and historical reference directories must remain preserved.

CLI and implementation SHA256 identities at handoff:

```text
a1186b0396f6116bf51b865c186150b7026a43af674a93ecc4262a6d4b3bab39  run.py
ecdd0dafef92763afc08eaa4eea6d9942a3146f4cc7f4f56478c1d8ec3eca64b  pins.json
96b206147441d0373320fe6218d4fdb8733ffbf46581838a4c4b8600e85de44f  core.py
eb6b8f75431cc346ec99cdc229e4bfc68072ba41d1be4f10f6d88a8a80373a8f  admission_scale.py
33b221570091003646d05cc727fcf34ae58de952026301d846233b1eeefde713  train_scale.py
```

`python3 -B scale-bc/run.py --help` succeeds without importing the learner or
executing game code. The staged patch passes `git diff --cached --check`.
