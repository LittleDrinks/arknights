# Scale BC

Only `scale-bc/` is added at base `04928de6615d7ebd7c53351971faf3657524c981`.
This is a future consumer of the frozen teacher-scale source integrated at `72c4c6c`.
It neither collects data nor runs evaluations. Development uses synthetic tests only.

Three arms start independently from the same original `all.pt`: `old6Reference`,
`newOnly`, and `combined`. Each calls the actual `bc-fit-budget/training.py`
`train_arm(..., epochs=15)`, which uses the actual `survivor-study/train.py` `Draws`
and original `rl/learner.py` `ActorCritic` / `bc_update`. There is no cached forward,
alternative architecture, continuation optimizer, recapture, or checkpoint selection.

| Contract | Value |
| --- | --- |
| Parent dimensions | `[5267,419,128]`, original CPU float32, eight parameters |
| Each arm | 15 epochs × 6800 draws = 102000 accesses; 3000 Adam updates |
| Training | lr `0.0003`, batch `34`, original gradient clip `0.5` |
| Order | continuous torch seed `101`; one randperm per epoch |
| Expansion | all eligible labels, original cyclic sample order |
| Old source | original 2048 plan; original admission, six wins / 498 labels |
| New source | exact seeds `32001..36096 × normal,mixed`, 8192 planned cases |

An arm with more than 6800 positive labels fails before training. The combined arm
therefore permits at most 6302 new labels. `--arms newOnly` permits up to 6800 new
labels without requiring the combined arm to fit. No labels are silently truncated.
No dev, heldout, sealed, failed-label pool, or outcome-based ranking enters training.
Normal/mixed on the same seed share an episode group. The old and new seed ranges
are disjoint. Only `features` and `action` reach L; `round` remains label provenance.
Seed, outcomes, source pool and terminal/private future fields never become actor inputs.

## Required immutable assets

| Asset | SHA256 |
| --- | --- |
| `ROOT/experiments/bc-expansion-v1/all.pt` | `f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a` |
| Original natural manifest | `dc8af9e7a99f96b2ec08dab3992c7512745ad84697770d632a904cbf7fb0463b` |
| Historical `steps3000.pt` | `af5f6ab22f02a86163d7990f8f63718591674a7243427801eda31a52f29c3b9b` |
| Historical `train.json` | `8a7e6c600066c8b6fb7c8606375f217961514141396d3b24260cea3df073e2ff` |

The old source is the original natural corpus, **not** the gold recapture directory.
The historical reference directory must retain `train.json`, `steps3000.pt`,
`input-metadata.json`, and `draw-plan.json`. The pinned report binds the last two
files. All other files in that directory are also inventoried and guarded against
changes. Its default path is `ROOT/experiments/bc-fit-budget-train-v1`.

The original `natural-win-bc/admission.py:admit` audits the entire old corpus; its
failed/not-run acceptance rules are retained exactly. We then reorder its 498
returned labels to the historical metadata order, checking exact artifact,
sample index, seed, pool and round membership. This preserves the original six
sample order even when it differs from manifest order. Training additionally
checks all 15 historical epoch permutations. The old reference arm must match
the frozen steps3000 **eight parameter hashes exactly** or the run fails. New
metadata changes serialization, so no checkpoint file-SHA equality is claimed.
Exact reproduction still depends on compatible numerical behavior of the server
PyTorch/CPU environment; a mismatch is reported, not weakened to a tolerance.

## Source admission and main's receipt

The new manifest must have `status=complete|incomplete`, `frozen=true`,
`globalFailed=false`, empty `globalErrors`, and a finish timestamp. `trainingAllowed`
must retain the collector's `false`; the separate bound receipt authorizes source
consumption. A running source is always rejected, even with a receipt.

The exact 8192 plan, all row identities/statuses, source-before/source-after,
game486/static data and pool identity are checked. Then the **existing**
`teacher-scale/reconcile.py:reconcile` independently reads every journal, request
log, raw/stage path and lane. Manifest games/lanes/files/digest must match this
independent result, including types. Unknown files, missing evidence, failed
lanes, fake completion, fake not-run, orphan raw and any reconciliation error
reject the batch. Incomplete sources can contain only genuinely reconciled
`not-run` rows plus complete rows. Explicit unspawned lane journals are accepted
under the existing reconciliation contract; completion is never fabricated.

Every complete raw, including negatives, passes compressed SHA, payload SHA,
gzip trailers/CRC/EOF, JSON newline and the original `A.complete_contract` over
the full terminal and every sample/diagnostic. Eligible journal flags are never
trusted in place of that audit. Strict positives require team victory, primary
alive individual victory at 14 rounds, and zero rejected labels. Complete deaths,
15-round wins and rejected-label episodes remain audited but unselected.

Main supplies a **full** new manifest SHA and independently produced receipt.
There is no interactive permission prompt or receipt generator that self-approves
the source. The receipt is JSON with the following shape; placeholders are not
accepted by the CLI:

```json
{
  "kind": "scale-bc-source-acceptance/v1",
  "accepted": true,
  "acceptedBy": "main/reviewer identifier",
  "acceptedAt": "ISO timestamp",
  "evidence": "independent source/corpus acceptance record location or digest",
  "bindings": {
    "newManifestSHA256": "FULL_FROZEN_NEW_MANIFEST_SHA",
    "oldManifestSHA256": "dc8af9e7a99f96b2ec08dab3992c7512745ad84697770d632a904cbf7fb0463b",
    "newSourceSHA256": "SHA_OF_CANONICAL_SOURCE_OBJECT",
    "gameSHA256": "SHA_OF_CANONICAL_GAME_OBJECT",
    "referenceReportSHA256": "8a7e6c600066c8b6fb7c8606375f217961514141396d3b24260cea3df073e2ff",
    "parentSHA256": "f04a8c895af05ddb8ad1b50d3e953270ddd7a6616d09097207b826fedfa3993a"
  }
}
```

The `source` and `game` objects come from the new corpus's `source-before.json`.
Their hashes are `C.digest(C.encoded(object))`: sorted keys, UTF-8, compact JSON,
nonfinite numbers forbidden, **one trailing newline**. The consumer also compares
these objects with live pinned code/game identities, so hashing an arbitrary
receipt is insufficient. Receipt SHA is the SHA of its exact file bytes. This is
a bound main-authored attestation, not a cryptographic reviewer signature and not
a replacement for the independent review referenced by `evidence`.

## Server commands, not executed in development

Use existing Linux Python 3.10+, PyTorch for `train`, and the game's existing
Node >=22 environment. No dependency installation is included. Relative paths
are relative to ROOT. Code must be this checkout; all required reused files are
pinned in `pins.json`, including transitive F/T imports and the entire collector
file set. The CLI imports reconciliation code but never calls a collector.

```bash
ROOT=/data1/zsm/arknights
CODE="$ROOT/experiment-code"
PYTHON=/absolute/path/to/existing/python
OLD=/absolute/path/to/original-natural-corpus
NEW=/absolute/path/to/frozen-8192-corpus
RECEIPT=/absolute/path/to/main-source-acceptance.json
NEW_SHA=FULL_FROZEN_MANIFEST_SHA256
RECEIPT_SHA=FULL_RECEIPT_FILE_SHA256

"$PYTHON" -B "$CODE/scale-bc/run.py" admission-only \
  --root "$ROOT" --old-input "$OLD" --new-input "$NEW" \
  --manifest-sha256 "$NEW_SHA" \
  --source-acceptance-receipt "$RECEIPT" --receipt-sha256 "$RECEIPT_SHA" \
  --out experiments/scale-bc-admission-v1

"$PYTHON" -B "$CODE/scale-bc/run.py" train \
  --root "$ROOT" --old-input "$OLD" --new-input "$NEW" \
  --manifest-sha256 "$NEW_SHA" \
  --source-acceptance-receipt "$RECEIPT" --receipt-sha256 "$RECEIPT_SHA" \
  --out experiments/scale-bc-train-v1
```

`--arms all` is the default. `--arms newOnly`, `--arms combined` or
`--arms old6Reference` runs only the requested arm, still with both corpora and
the historical reference independently admitted. All arms start from original
parent parameters with a fresh independent Adam. An admission-only result is
evidence, not a reusable bypass: `train` repeats full admission.

Default limits: cumulative **900 seconds other work**, including training and
static guards, plus a **separate cumulative 1800 seconds full corpus audit and
inventory rechecks**, total 2700 seconds. Flags `--work-seconds` and
`--audit-seconds` each allow 1..7200, always finite. The audit limit covers full
8192 raw reconciliation plus old admission and before/after inventory hashing;
there is no production timing claim that it will fit. Exhaustion fails closed.
The final bounded report/fsync and uninterruptible kernel I/O are not a hard
real-time guarantee.

`--ram-gib` defaults to 16 (range 1..16). Each process inherits a conservative
address-space ceiling of half this allowance, default 8 GiB. At most the parent
and one sequential static guard child coexist, bounding their aggregate RSS
by 16 GiB; training uses no subprocesses. Existing tighter limits are retained.
`--output-mib` defaults to 256 (16..1024); all output content, including a 4 MiB
final-report reserve, counts against it. Output cannot overlap inputs/source/game,
must be new with an existing parent, and rejects symlink components. Writes use
an exclusive retained directory descriptor. Input special files and all symlinks
are rejected. Source, receipt, parent, whole input inventories, game486/static
data and output identities are guarded before/after; no source file is rewritten.
CPU affinity only narrows the inherited set to at most four CPUs; original Torch
intra/inter-op threads are one.

## Artifacts and interpretation

- `admission.json` or `train.json`: final status, limits, source/game identities,
  bound inputs, final checks, output hashes and per-arm results. Exit 0 requires
  complete status and all guards; any exception leaves failure evidence and exit 2.
  A checkpoint in a failed run is not an accepted result.
- `source-before.json`, `source-after.json`, copied acceptance receipt, old/new
  frozen manifests, old per-case audit records, compressed old/new metadata and
  `admission-inventories.json.gz`: full provenance/inventory evidence. Full raw
  corpora remain read-only at their original locations; they are not duplicated
  into the 256 MiB output directory.
- `historical-draw-plan.json.gz` and `draw-plan.json.gz`: bound reference order,
  selected indices, cyclic expansion and all epoch permutations.
- `<arm>-accesses.json.gz`: every actual Draws access and resulting label index.
  Results record container-order SHA, actual label-order SHA, per-label counts,
  per-pool counts, episode-group counts, initial/final full parameter SHA, each of
  eight parameter SHAs, each trained parameter's Adam step and critic-head SHA.
- `old6Reference.pt`, `newOnly.pt`, `combined.pt` for requested arms: standard L
  `model/dimensions/schema/rules/metadata/optimizer/torch_rng` format. Original
  loader/validator and all eight exact tensors verify roundtrip. Critic **head**
  stays unchanged; the shared encoder and therefore value outputs can change.

There is no evaluation CLI. Main may use the standard checkpoints with existing
`P.game_job` after its approval. Training accuracy/NLL are training diagnostics,
not wins or generalization evidence. There is no dev/heldout-based selection.

## Local verification

```bash
/home/q2635/venvs/logics-v3/bin/python -B scale-bc/check.py
```

The inherited synthetic supervisor kills at 19 seconds and monitors the complete
test process tree for <=1 GiB sampled RSS and <=5,000,000 temporary output bytes.
Tests fabricate tiny payloads on the full plan, not real 8192 trajectories. They
exercise actual reconciliation/complete_contract, legacy old admission, source
receipt binding, gzip EOF/newline, missing evidence, consistent-SHA semantic
forgeries, full terminal/sample checks on negatives, symlinks/special files,
output collisions, selection/group coverage and original tiny-model training.
The CLI admission path and final-guard failure are tested with synthetic
substitutions for external historical assets/static game identity. See CHECKS.md
for measured results and explicit unexecuted real checks.
