#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if curl -fsS --max-time 3 http://127.0.0.1:38181/healthz >/dev/null; then
  echo 'Preview already running on port 38181'
  exit 0
fi
mkdir -p "$task_root/logs"
cd "$task_root/game"
nohup env PORT=38181 HOST=0.0.0.0 SP_ASSET_INDEX=0 node server/index.js > "$task_root/logs/frontend.log" 2>&1 < /dev/null &
echo "$!" > "$task_root/logs/frontend.pid"
echo 'Started preview on port 38181; open /?replay=solo-ABYSS-s1'
