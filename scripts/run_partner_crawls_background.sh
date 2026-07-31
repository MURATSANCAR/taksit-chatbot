#!/usr/bin/env bash
# Keep partner catalog crawls alive (ADR-010). Resume-safe via feed checkpoints.
# Stops starting new crawls when live feed product total >= CRAWL_GLOBAL_PRODUCT_CAP (default 1M).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv-crawl/bin/python"
LOG=/tmp/taksitlio-partner-crawls
mkdir -p "$LOG"
export CRAWL_GLOBAL_PRODUCT_CAP="${CRAWL_GLOBAL_PRODUCT_CAP:-1000000}"

at_global_cap() {
  "$PY" - <<'PY'
import os, sys
sys.path.insert(0, "scripts")
from fetch_live_merchant_feeds import count_live_feed_products, set_global_product_cap, active_global_product_cap
cap = int(os.environ.get("CRAWL_GLOBAL_PRODUCT_CAP", "1000000"))
set_global_product_cap(cap)
total = count_live_feed_products()
print(f"live_total={total} global_cap={active_global_product_cap() or 'disabled'}")
sys.exit(0 if (cap <= 0 or total < cap) else 1)
PY
}

if ! at_global_cap; then
  echo "global product cap reached — not starting new crawls"
  exit 0
fi

run() {
  local name="$1"; shift
  if pgrep -f "fetch_live_merchant_feeds.py --merchants ${name}" >/dev/null 2>&1; then
    echo "already running: $name"
    return 0
  fi
  if ! at_global_cap >/dev/null; then
    echo "skip $name — global product cap reached"
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
  if at_global_cap >/dev/null; then
    nohup "$PY" -u scripts/fetch_teknosa_crawl4ai.py --limit 0 --concurrency 3 \
      >"$LOG/teknosa.log" 2>&1 &
    echo "pid $! teknosa"
  else
    echo "skip teknosa — global product cap reached"
  fi
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
