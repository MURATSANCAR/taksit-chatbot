#!/usr/bin/env bash
# Keep partner catalog crawls alive (ADR-010). Resume-safe via feed checkpoints.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv-crawl/bin/python"
LOG=/tmp/taksitlio-partner-crawls
mkdir -p "$LOG"

run() {
  local name="$1"; shift
  if pgrep -f "fetch_live_merchant_feeds.py --merchants ${name}" >/dev/null 2>&1; then
    echo "already running: $name"
    return 0
  fi
  echo "start $name $*"
  nohup "$PY" -u scripts/fetch_live_merchant_feeds.py --merchants "$name" "$@" \
    >"$LOG/${name}.log" 2>&1 &
  echo "pid $! -> $LOG/${name}.log"
}

run flo --delay 0.15 --workers 8 --limit 0
run n11 --delay 0.12 --workers 8 --limit 0
run trendyol --delay 0.25 --workers 4 --limit 0
run dr --delay 0.12 --workers 10 --limit 0
run mediamarkt --delay 0.12 --workers 10 --limit 0
run network --delay 0.15 --workers 6 --limit 0
run civil --delay 0.15 --workers 6 --limit 0
run arcelik --delay 0.15 --workers 4 --limit 0
run beko --delay 0.15 --workers 4 --limit 0

if ! pgrep -f "fetch_teknosa_crawl4ai.py" >/dev/null 2>&1; then
  nohup "$PY" -u scripts/fetch_teknosa_crawl4ai.py --limit 0 --concurrency 3 \
    >"$LOG/teknosa.log" 2>&1 &
  echo "pid $! teknosa"
fi

echo "status:"
"$PY" - <<'PY'
import json
from pathlib import Path
t=0
for p in sorted(Path("crawler/feeds/live").glob("src-m-*.json")):
  n=json.loads(p.read_text()).get("count") or 0
  t+=n
  print(f"  {p.name}: {n}")
print(f"  TOTAL: {t}")
PY
