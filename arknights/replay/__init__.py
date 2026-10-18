"""Verify saved player commands and open their native game recording."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def _safe(path):
    path = Path(os.path.abspath(path))
    if ".." in Path(path).parts:
        raise ValueError("Path traversal")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symlink in replay path: " + str(part))
    return path


def _json(path):
    return json.loads(_safe(path).read_text())


def _atomic(path, value):
    path = _safe(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _publish(root, bundle):
    """Append verified artifacts, preserving both original indexes and every file."""
    root, bundle = _safe(root), _safe(bundle)
    live = _safe(root / "results/replays/public/dev")
    live.mkdir(parents=True, exist_ok=True)
    report = _json(bundle / "verification.json")
    verified = {}
    for row in report["files"]:
        name = row["file"]
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or name in verified:
            raise ValueError("Invalid/duplicate replay artifact path")
        source = _safe(bundle / relative)
        data = source.read_bytes()
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Replay artifact changed: " + name)
        verified[name] = data
    if not {"agent/index.json", "recordings/index.json"} <= verified.keys():
        raise ValueError("Unverified replay indexes")
    agent = json.loads(verified["agent/index.json"])
    recordings = json.loads(verified["recordings/index.json"])
    if not agent.get("episodes"):
        raise ValueError("No replay episodes")
    for ep in agent["episodes"]:
        name = "agent/" + ep["file"]
        if name not in verified or json.loads(verified[name]).get("meta", {}).get("id") != ep["id"]:
            raise ValueError("Missing/mismatched indexed episode")
    for recording in recordings:
        name = "recordings/" + recording["file"]
        if name not in verified or json.loads(verified[name]).get("name") != recording["name"]:
            raise ValueError("Missing/mismatched indexed recording")
    lock = root / "results/replays/.publish.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        ap, rp = _safe(live / "agent/index.json"), _safe(live / "recordings/index.json")
        old_a = _json(ap) if ap.exists() else {**agent, "episodes": []}
        old_r = _json(rp) if rp.exists() else []
        ids = [x["id"] for x in old_a["episodes"] + agent["episodes"]]
        names = [x["name"] for x in old_r + recordings]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("Replay index collision; existing recordings retained")
        if old_a["schema"] != agent["schema"]:
            raise ValueError("Replay index schema differs")
        merged = {**old_a, "episodes": old_a["episodes"] + agent["episodes"]}
        merged["totalSteps"] = sum(x["steps"] for x in merged["episodes"])
        merged["totalBytes"] = sum(x.get("bytes", 0) for x in merged["episodes"])
        all_recordings = old_r + recordings
        refs = {(x["name"], x["file"]) for x in all_recordings}
        for ep in merged["episodes"]:
            if any((b["name"], b["file"]) not in refs for b in ep.get("battles", [])):
                raise ValueError("Missing referenced battle")
        pending = []
        for name, data in verified.items():
            if name in ("agent/index.json", "recordings/index.json"):
                continue
            relative = Path(name)
            if relative.parts[0] not in ("agent", "recordings"):
                continue  # Verified auxiliary artifacts remain in the immutable bundle.
            target = _safe(live / relative)
            if target.exists():
                raise FileExistsError(target)
            pending.append((target, data))
        _atomic(bundle / "indexes-before.json", {"agent": old_a, "recordings": old_r})
        for target, data in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
        ap.parent.mkdir(parents=True, exist_ok=True)
        rp.parent.mkdir(parents=True, exist_ok=True)
        try:
            _atomic(rp, all_recordings)
            _atomic(ap, merged)
        except BaseException:
            _atomic(rp, old_r)
            _atomic(ap, old_a)
            raise
        _atomic(bundle / "publication.json", {"episodes": [e["id"] for e in agent["episodes"]],
            "totalEpisodes": len(merged["episodes"]), "totalRecordings": len(all_recordings)})
        return agent["episodes"][0]["id"]
    finally:
        os.close(fd)
        lock.unlink()


def preview(*, root=None, port=38181):
    """Start/reuse only this project's viewer; never replace an unrelated listener."""
    from arknights.adapter import RULES
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    address = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(address + "/healthz", timeout=2) as response:
            health = json.load(response)
        if health.get("arknightsPreview") != 1 or health.get("rules") != RULES or health.get("root") != str(root) or health.get("replayDir") is not None:
            raise RuntimeError("Port is occupied by a different viewer: " + str(port))
        return address
    except urllib.error.URLError:
        pass
    logs = root / "results/logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / f"preview-{port}.log").open("ab") as log:
        child = subprocess.Popen([shutil.which("node") or "node", str(Path(__file__).with_name("server.mjs")),
            "--port", str(port)], cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    try:
        _atomic(logs / f"preview-{port}.json", {"pid": child.pid, "root": str(root), "port": port})
        for _ in range(50):
            if child.poll() is not None:
                raise RuntimeError("Viewer exited; see " + str(logs / f"preview-{port}.log"))
            try:
                with urllib.request.urlopen(address + "/healthz", timeout=.5) as response:
                    health = json.load(response)
                if (health.get("arknightsPreview") == 1 and health.get("root") == str(root)
                        and health.get("rules") == RULES and health.get("replayDir") is None):
                    return address
            except urllib.error.URLError:
                pass
            time.sleep(.1)
        raise TimeoutError("Viewer did not become ready")
    except BaseException:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        raise



def replay(run, episode=None, *, root=None, port=38181, timeout=180, serve=True):
    """Re-execute saved commands, verify every response, then record and publish.

    This is saved-command replay, not another policy inference or a new score.
    Partial/failed evaluations cannot be published as complete recordings.
    """
    from arknights.evaluate.runner import verify_run
    from arknights.evaluate.process import JsonlProcess
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    path = Path(run.path if hasattr(run, "path") else run)
    if not path.is_absolute():
        path = root / "results/runs" / path
    path = path.resolve()
    verify_run(path)
    summary = _json(path / "summary.json")
    rows = summary["games"]
    row = next((r for r in rows if r["id"] == episode), None) if episode else next((r for r in rows if r["status"] == "complete"), None)
    if row is None or row["status"] != "complete":
        raise ValueError("Select a complete episode from summary.json")
    source = path / row["id"] / "trajectory"
    requests = [json.loads(line)["request"] for line in (source / "requests.jsonl").read_text().splitlines()]
    responses = [json.loads(line) for line in (source / "protocol.jsonl").read_text().splitlines()]
    # Process.close can append a close acknowledgment after the recorded requests.
    if len(responses) not in (len(requests), len(requests) + 1):
        raise ValueError("Incomplete request/response inventory")
    token = uuid.uuid4().hex[:12]
    output = root / "results/replays" / (path.name + "-" + row["id"] + "-" + token)
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / "bundle"
    command = [shutil.which("node") or "node", str(root / "arknights/adapter/backend.mjs"),
               "--root", str(root), "--record-dir", str(bundle)]
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ARK_", "COOP_", "STRONGHOLD_"))
           and k not in ("NODE_OPTIONS", "NODE_PATH")}
    started = time.monotonic()
    with JsonlProcess(command, cwd=root, env=env, timeout=timeout,
                      raw_path=output / "protocol.jsonl", stderr_path=output / "stderr.log",
                      max_bytes=512 * 1024**2) as child:
        for i, (request, expected) in enumerate(zip(requests, responses)):
            if time.monotonic() - started >= timeout:
                raise TimeoutError("Replay deadline")
            if request["cmd"] == "close":
                continue
            actual_request = {**request}
            if request["cmd"] == "reset":
                actual_request["record"] = True
            actual = child.request(actual_request)
            if actual != expected:
                _atomic(output / "mismatch.json", {"requestIndex": i, "request": request,
                    "expected": expected, "actual": actual})
                raise ValueError(f"Replay response differs at request {i}; evidence retained in {output}")
        report = child.request({"cmd": "recording"})
        if not report.get("ok"):
            raise RuntimeError(report)
    _atomic(output / "reproduction.json", {"sourceRun": str(path), "episode": row["id"],
        "execution": "saved-command-replay-only", "inferenceRerun": False,
        "verifiedResponses": len(requests), "terminal": row.get("teamVictory"), "recording": report})
    verify_run(path)
    identifier = _publish(root, bundle)
    address = preview(root=root, port=port) if serve else f"http://127.0.0.1:{port}"
    return {"url": address + "/?replay=" + identifier, "path": str(output), "episode": identifier}


__all__ = ["replay", "preview"]
