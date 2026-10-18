"""A player session: observe, act, wait, lookup. No automatic player decisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import shutil


RULES = "6ab2468279e926d107788f63d7fe219b85f67b4a"


def _encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


class Session:
    """Own one game. In evaluation the framework, rather than play(), owns this context.

    State and replies are JSON dictionaries. ``act`` returns accepted/result/state;
    rejected player operations are data, transport and engine failures are exceptions.
    Queries and observations never advance the virtual game clock.
    """

    def __init__(self, *, root=None, directory=None, timeout=120, max_requests=4096, **config):
        self.root = Path(root or Path(__file__).resolve().parents[2]).resolve()
        self.directory = Path(directory) if directory is not None else None
        self.timeout, self.max_requests = timeout, max_requests
        self.config, self.state = config, None
        self._child = self._requests = None
        self._count = 0

    def __enter__(self):
        from arknights.evaluate.process import JsonlProcess
        if self._child is not None:
            raise RuntimeError("Session already started")
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=False)
            self._requests = (self.directory / "requests.jsonl").open("xb")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ARK_", "COOP_", "STRONGHOLD_"))
               and k not in ("NODE_OPTIONS", "NODE_PATH")}
        command = [shutil.which("node") or "node", str(Path(__file__).with_name("backend.mjs")),
                   "--root", str(self.root)]
        try:
            self._child = JsonlProcess(command, cwd=self.root, env=env, timeout=self.timeout,
                raw_path=self.directory / "protocol.jsonl" if self.directory else None,
                stderr_path=self.directory / "stderr.log" if self.directory else None)
            self._request({"cmd": "reset", **self.config})
            return self
        except BaseException:
            self.close()
            raise

    def _request(self, request):
        if self._child is None:
            raise RuntimeError("Use Session as a context manager")
        if self._count >= self.max_requests:
            raise RuntimeError("Strategy request budget exhausted; incomplete game")
        self._count += 1
        # Keep the request even if its response times out or is malformed.
        if self._requests:
            self._requests.write(_encoded({"seq": self._count, "request": request}))
            self._requests.flush()
        response = self._child.request(request)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Game protocol failure"))
        if response.get("result", {}).get("error") == "ADAPTER_ERROR":
            raise RuntimeError(response["result"].get("detail", "Adapter execution failed"))
        if "state" in response:
            self.state = response["state"]
            if self.state.get("rules") != RULES or self.state.get("apiVersion") != 1:
                raise RuntimeError("Game rules or adapter protocol changed")
            if self.directory and self.state.get("terminal"):
                p = self.directory / "terminal.json"
                if not p.exists():
                    with p.open("xb") as stream:
                        stream.write(_encoded(self.state))
        return response

    def observe(self):
        return self._request({"cmd": "observe"})["state"]

    def act(self, action):
        return self._request({"cmd": "act", "action": action})

    def wait(self):
        return self._request({"cmd": "wait"})["state"]

    def lookup(self, query):
        return self._request({"cmd": "lookup", "query": query})["result"]

    def close(self):
        child, self._child = self._child, None
        try:
            if child is not None:
                child.close()
        finally:
            if self._requests:
                self._requests.close()
                self._requests = None

    def __exit__(self, *_):
        self.close()


__all__ = ["Session", "RULES"]
