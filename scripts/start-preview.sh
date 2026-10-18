#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if curl -fsS --max-time 3 http://127.0.0.1:38181/healthz >/dev/null; then
  echo 'Preview already running on port 38181'
  exit 0
fi
mkdir -p "$project_root/results/logs"
cd "$project_root"
nohup node adapters/web/server.mjs > results/logs/frontend.log 2>&1 < /dev/null &
echo "$!" > results/logs/frontend.pid
echo 'Started preview on port 38181'
