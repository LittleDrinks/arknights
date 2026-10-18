"""One entrypoint for strategy evaluation, saved-action replay and historical tools."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def seeds(value):
    try:
        if ":" in value:
            first, last = map(int, value.split(":"))
            if not 1 <= first <= last <= 0xffffffff or last - first > 10000:
                raise ValueError()
            return list(range(first, last + 1))
        result = list(map(int, value.split(",")))
        if not result or len(result) != len(set(result)) or any(not 1 <= s <= 0xffffffff for s in result):
            raise ValueError()
        return result
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use inclusive first:last or comma-separated uint32 seeds") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ev = commands.add_parser("evaluate", help="Run play(game) from a Python strategy script")
    ev.add_argument("script", type=Path)
    ev.add_argument("--suite", choices=("abyss-dev", "smoke"), default="abyss-dev")
    ev.add_argument("--seeds", type=seeds)
    ev.add_argument("--partners", nargs="+", choices=("original", "mixed", "weak"))
    ev.add_argument("--mode", choices=("solo", "coop"), default="coop")
    ev.add_argument("--difficulty", choices=("NORMAL", "HARD", "ABYSS"), default="ABYSS")
    ev.add_argument("--workers", type=int, default=1)
    ev.add_argument("--timeout", type=float, default=120)
    ev.add_argument("--plan", action="store_true", help="Print resolved case plan without running a game")
    ev.add_argument("--queue", action="store_true", help="Submit a persistent job to the evaluation dashboard")
    ev.add_argument("--label", help="Display name for queued evaluation")
    ev.add_argument("--port", type=int, default=38181, help="Viewer/queue port")
    rep = commands.add_parser("replay", help="Verify and record a complete saved episode")
    rep.add_argument("run")
    rep.add_argument("--episode")
    rep.add_argument("--port", type=int, default=38181)
    rep.add_argument("--timeout", type=float, default=180)
    rep.add_argument("--no-serve", action="store_true")
    serve = commands.add_parser("preview", help="Start/reuse the native viewer")
    serve.add_argument("--port", type=int, default=38181)
    collect = commands.add_parser("collect", help="Human demonstration capture and export")
    collect_commands = collect.add_subparsers(dest="collect_command", required=True)
    collect_serve = collect_commands.add_parser("serve")
    collect_serve.add_argument("--port", type=int, default=38181)
    collect_export = collect_commands.add_parser("export")
    collect_export.add_argument("collection_id")
    collect_export.add_argument("--profile", choices=("human-reviewed", "all-human"), default="human-reviewed")
    commands.add_parser("check", help="Check the frozen game checkout")
    legacy = commands.add_parser("legacy", help="Historical SHA-pinned experiments (compatibility only)")
    legacy.add_argument("arguments", nargs=argparse.REMAINDER)
    commands.add_parser("vendor", help="Download browser libraries into ignored runtime")
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        from arknights.evaluate import evaluate
        selected = args.seeds if args.seeds is not None else ([17] if args.suite == "smoke" else list(range(1, 17)))
        partners = args.partners or (["original"] if args.suite == "smoke" else ["original", "mixed"])
        if args.plan:
            print(json.dumps(dict(script=str(args.script.resolve()), suite=args.suite, mode=args.mode,
                difficulty=args.difficulty, seeds=selected, partners=partners, workers=args.workers,
                timeout=args.timeout, cases=len(selected) * (1 if args.mode == "solo" else len(partners))), indent=2))
            return
        if args.queue:
            from arknights.evaluate import submit
            job = submit(args.script, root=ROOT, port=args.port, label=args.label,
                seeds=",".join(map(str, selected)), mode=args.mode, difficulty=args.difficulty,
                partners=partners, workers=args.workers, timeout=args.timeout)
            print(json.dumps(job, ensure_ascii=False, indent=2))
            return
        run = evaluate(args.script, root=ROOT, seeds=selected, mode=args.mode, difficulty=args.difficulty,
                       partners=partners, timeout=args.timeout, workers=args.workers)
        print(json.dumps({"run": str(run.path), "summary": run.summary}, ensure_ascii=False, indent=2))
        if run.summary.get("status") != "complete":
            raise SystemExit(1)
    elif args.command == "replay":
        from arknights.replay import replay
        print(json.dumps(replay(args.run, args.episode, root=ROOT, port=args.port,
                                timeout=args.timeout, serve=not args.no_serve), ensure_ascii=False, indent=2))
    elif args.command == "preview":
        from arknights.replay import preview
        print(preview(root=ROOT, port=args.port))
    elif args.command == "collect":
        from arknights.collect import serve, export
        if args.collect_command == "serve":
            print(serve(root=ROOT, port=args.port).url)
        else:
            print(json.dumps(export(args.collection_id, args.profile, root=ROOT).metadata, ensure_ascii=False, indent=2))
    elif args.command == "check":
        from arknights.adapter import RULES
        actual = subprocess.check_output(["git", "-C", str(ROOT / "game"), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(ROOT / "game"), "status", "--porcelain", "--untracked-files=all"])
        if actual != RULES or dirty.strip():
            raise RuntimeError("game must match the pinned revision and remain pristine")
        print(json.dumps({"rules": actual, "game": "clean"}))
    elif args.command == "vendor":
        subprocess.run(["node", str(ROOT / "arknights/evaluate/_legacy/scripts/vendor.mjs")], check=True)
    else:
        subprocess.run([sys.executable, "-B", str(ROOT / "arknights/evaluate/_legacy/scripts/ark.py"), *args.arguments], check=True)


if __name__ == "__main__":
    main()
