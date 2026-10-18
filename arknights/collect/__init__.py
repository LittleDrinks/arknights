"""Explicit-consent human capture. Export uses recorded events, never virtual re-execution."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


@dataclass
class CaptureHandle:
    url: str
    _process: object = field(default=None, repr=False)

    def close(self):
        """Close only the service this handle started. Reused viewers remain running."""
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=15)


@dataclass(frozen=True)
class DatasetRef:
    path: Path
    sha256: str
    version: str
    quality: dict
    metadata: dict = field(repr=False)


def serve(config=None, *, root=None, port=38181):
    """Start/reuse this root's capable viewer; refuse occupied or unsupported hosts."""
    from arknights.adapter import RULES
    config = dict(config or {})
    root = Path(config.get("root", root or Path(__file__).resolve().parents[2])).resolve()
    port = config.get("port", port)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    address = f"http://127.0.0.1:{port}"

    def health():
        with urllib.request.urlopen(address + "/healthz", timeout=1) as response:
            h = json.load(response)
        if (h.get("arknightsPreview") != 1 or h.get("rules") != RULES or h.get("root") != str(root)
                or h.get("replayDir") is not None or not h.get("collect", {}).get("supported")):
            raise RuntimeError("Occupied viewer does not support capture for this root; upgrade when rooms have ended")
        return CaptureHandle(address + "/collect")

    try:
        return health()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Occupied service has no collection capability") from exc
    except urllib.error.URLError:
        pass
    logs = root / "results/logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / f"collect-{port}.log").open("ab") as stream:
        child = subprocess.Popen(["node", str(root / "arknights/replay/server.mjs"), "--port", str(port)],
                                 cwd=root, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                 start_new_session=True)
    try:
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError("Viewer failed to start; see " + str(logs / f"collect-{port}.log"))
            try:
                handle = health()
                handle._process = child
                return handle
            except urllib.error.URLError:
                time.sleep(.1)
        raise TimeoutError("Viewer startup did not become healthy")
    except BaseException:
        child.terminate()
        child.wait(timeout=15)
        raise


def export(collection_id, profile="human-reviewed", *, root=None):
    """Write an immutable versioned sample dataset and return its hashes and limits."""
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    result = subprocess.run(["node", str(Path(__file__).with_name("cli.mjs")), str(root), collection_id, profile],
                            check=True, capture_output=True, text=True)
    value = json.loads(result.stdout)
    return DatasetRef(Path(value["path"]), value["sha256"], value["version"], value["quality"], value)


__all__ = ["serve", "export", "CaptureHandle", "DatasetRef"]
