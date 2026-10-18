"""Run paired upstream-bot rehearsal ablations, preserving raw per-seed outcomes."""
import argparse
import datetime
import json
import os
import hashlib
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--game", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--seeds", type=int, default=16)
parser.add_argument("--isolate-planning-rng", action="store_true")
args = parser.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
manifest = {"experiment": "upstream planner rehearsal candidates", "status": "running",
            "engine_commit": "6ab2468279e926d107788f63d7fe219b85f67b4a",
            "seed_start": 1, "seed_count_per_mode_per_arm": args.seeds,
            "configs": ["solo:ABYSS", "coop4:ABYSS"], "jobs": 4,
            "trained_model": False, "arms": [],
            "planning_rng_isolation": args.isolate_planning_rng,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "interpretation": "Same seeded starts, different simulation budgets; not an equal-compute training ablation. Host load inflates jobs=4 timings; process CPU time is shared across workers and is not per-match CPU."}
def save():
    (out / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
save()
loader = Path(__file__).resolve().with_name('isolate-planning-rng.mjs')
if args.isolate_planning_rng:
    manifest['loader_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [loader,loader.with_name('planning-rng-loader.mjs')]}
    save()
for candidates in [0, 1, 3]:
    prefix=['node','--import',str(loader)] if args.isolate_planning_rng else ['node']
    cmd = prefix + ["tools/botbench.mjs", "--configs", ",".join(manifest["configs"]),
           "--seed", "1", "--seeds", str(args.seeds), "--jobs", "4",
           "--rehearsal", str(candidates), "--quiet", "--json", str(out / f"r{candidates}.json")]
    start = time.monotonic()
    with (out / f"r{candidates}.log").open("w") as log:
        env=dict(os.environ,ARK_GAME_ROOT=str(args.game.resolve()))
        result = subprocess.run(cmd, cwd=args.game, env=env, stdout=log, stderr=subprocess.STDOUT)
    arm = {"rehearsal": candidates, "command": cmd, "exit_code": result.returncode,
           "batch_wall_seconds": time.monotonic() - start}
    manifest["arms"].append(arm)
    if result.returncode:
        manifest["status"] = "failed"
        save()
        raise SystemExit(result.returncode)
    save()
    print(json.dumps(arm), flush=True)
manifest["status"] = "complete"
manifest["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
save()
