#!/usr/bin/env python3
"""Autonomous partner crawl + ingest watchdog (nanobase only).

- Never waits for interactive input
- On crawl/ingest failure: log, skip/retry later, continue queue
- Restarts dead workers
- Hard stop at CRAWL_GLOBAL_PRODUCT_CAP (default 1_000_000)

  nohup .venv-crawl/bin/python -u scripts/auto_partner_ops.py > /tmp/auto-partner-ops.log 2>&1 &
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_CRAWL = ROOT / ".venv-crawl" / "bin" / "python"
PY_APP = ROOT / ".venv" / "bin" / "python"
FEED = ROOT / "crawler" / "feeds" / "live"
LOG = Path("/tmp/taksitlio-partner-crawls")
STATE = Path("/tmp/taksitlio-auto-ops-state.json")

TARGET = int(os.environ.get("CRAWL_GLOBAL_PRODUCT_CAP", "1000000"))
# When set: do not start new merchant crawls — only ingest/backfill/completeness.
COMPLETE_ONLY = os.environ.get("AUTO_COMPLETE_ONLY", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

# (code, delay, workers) — fast first; slow/WAF last
CRAWL_QUEUE: list[tuple[str, float, int]] = [
    ("trendyol", 0.15, 8),
    ("mediamarkt", 0.08, 12),
    ("civil", 0.1, 8),
    ("evofone", 0.15, 6),
    ("network", 0.1, 8),
    ("teknosa", 0.12, 6),
    ("flo", 0.12, 6),
    ("arcelik", 0.2, 3),
    ("beko", 0.2, 3),
    ("dr", 0.1, 10),
    ("n11", 0.15, 4),
    ("decathlon", 0.5, 2),
]

SKIP_IF_AT_LEAST = {
    "flo": 150_000,
    "teknosa": 7_000,
    "network": 6_000,
    "arcelik": 1_000,
    "beko": 1_000,
    "evofone": 120,
    "civil": 2_000,
    "mediamarkt": 5_000,
    "dr": 5_000,
    "trendyol": 80_000,
    "n11": 80_000,
    "decathlon": 5_000,
}

# After N consecutive fails, cool down this many seconds before retry
FAIL_COOLDOWN_S = 1800
MAX_PARALLEL_CRAWLS = int(os.environ.get("AUTO_MAX_PARALLEL_CRAWLS", "4"))
INGEST_AFTER_GROWTH = int(os.environ.get("AUTO_INGEST_MIN_NEW", "200"))

_stop = False


def _on_signal(signum, frame) -> None:  # noqa: ARG001
    global _stop
    _stop = True
    print(f"signal {signum} — graceful stop after current cycle", flush=True)


signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT, _on_signal)


def feed_count(code: str) -> int:
    path = FEED / f"src-m-{code}.json"
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("count") or 0)
    except Exception:
        return 0


def total_feeds() -> int:
    total = 0
    for path in FEED.glob("src-m-*.json"):
        try:
            total += int(json.loads(path.read_text(encoding="utf-8")).get("count") or 0)
        except Exception:
            continue
    return total


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"fails": {}, "last_ingest_feed_total": 0, "round": 0}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def pgrep(pattern: str) -> list[int]:
    r = subprocess.run(
        ["pgrep", "-f", pattern], capture_output=True, text=True
    )
    if r.returncode != 0:
        return []
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


def running_crawl_codes() -> set[str]:
    out: set[str] = set()
    r = subprocess.run(["ps", "ax", "-o", "args="], capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        if "fetch_live_merchant_feeds.py" not in line:
            continue
        if "--merchants" not in line:
            continue
        try:
            part = line.split("--merchants", 1)[1].strip().split()[0]
            for code in part.split(","):
                code = code.strip()
                if code:
                    out.add(code)
        except Exception:
            continue
    return out


def start_crawl(code: str, delay: float, workers: int) -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    log = LOG / f"auto-{code}.log"
    env = os.environ.copy()
    env["CRAWL_GLOBAL_PRODUCT_CAP"] = str(TARGET)
    print(
        f"START crawl {code} delay={delay} workers={workers} "
        f"before={feed_count(code)} total={total_feeds()}/{TARGET}",
        flush=True,
    )
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- auto start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        subprocess.Popen(
            [
                str(PY_CRAWL),
                "-u",
                str(ROOT / "scripts" / "fetch_live_merchant_feeds.py"),
                "--merchants",
                code,
                "--delay",
                str(delay),
                "--workers",
                str(workers),
                "--limit",
                "0",
                "--global-cap",
                str(TARGET),
            ],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )


def crawl_done_ok(code: str, before: int) -> bool:
    """Heuristic: grew, or already at skip threshold, or log shows blocked once."""
    after = feed_count(code)
    if after > before:
        return True
    thresh = SKIP_IF_AT_LEAST.get(code)
    if thresh is not None and after >= thresh:
        return True
    log = LOG / f"auto-{code}.log"
    if log.exists():
        tail = log.read_text(encoding="utf-8", errors="ignore")[-4000:]
        if "sitemap index blocked" in tail or "sitemap index HTTP 403" in tail:
            return False
    return after >= before  # finished without crash counts as attempt


def should_skip(code: str, st: dict) -> bool:
    thresh = SKIP_IF_AT_LEAST.get(code)
    if thresh is not None and feed_count(code) >= thresh:
        return True
    fails = st.get("fails") or {}
    info = fails.get(code) or {}
    until = float(info.get("cooldown_until") or 0)
    if time.time() < until:
        return True
    return False


def mark_fail(st: dict, code: str) -> None:
    fails = st.setdefault("fails", {})
    info = fails.setdefault(code, {"count": 0})
    info["count"] = int(info.get("count") or 0) + 1
    info["cooldown_until"] = time.time() + FAIL_COOLDOWN_S
    info["last_fail"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"FAIL {code} count={info['count']} cooldown={FAIL_COOLDOWN_S}s — continue",
        flush=True,
    )


def mark_ok(st: dict, code: str) -> None:
    fails = st.setdefault("fails", {})
    if code in fails:
        fails[code]["count"] = 0
        fails[code]["cooldown_until"] = 0


def ingest_running() -> bool:
    return bool(pgrep("ingest_live_feeds_postgres.py"))


def start_ingest(merchants: str) -> None:
    if not PY_APP.is_file():
        print("skip ingest — .venv missing", flush=True)
        return
    log = Path("/tmp/auto-ingest.log")
    env = os.environ.copy()
    # load .env.runtime if present
    env_file = ROOT / ".env.runtime"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    print(f"START ingest merchants={merchants}", flush=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- auto ingest {time.strftime('%Y-%m-%d %H:%M:%S')} {merchants} ---\n")
        subprocess.Popen(
            [
                str(PY_APP),
                "-u",
                str(ROOT / "scripts" / "ingest_live_feeds_postgres.py"),
                "--merchants",
                merchants,
                "--skip-campaigns",
            ],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )


def start_backfill_if_idle() -> None:
    if pgrep("backfill_product_images.py"):
        return
    if not PY_APP.is_file():
        return
    env_file = ROOT / ".env.runtime"
    env = os.environ.copy()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    log = Path("/tmp/auto-backfill.log")
    print("START image backfill", flush=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- auto backfill {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        subprocess.Popen(
            [
                str(PY_APP),
                "-u",
                str(ROOT / "scripts" / "backfill_product_images.py"),
                "--concurrency",
                "8",
                "--limit",
                "0",
            ],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )


def fill_crawl_slots(st: dict) -> None:
    if COMPLETE_ONLY:
        return
    running = running_crawl_codes()
    slots = max(0, MAX_PARALLEL_CRAWLS - len(running))
    if slots <= 0:
        return
    for code, delay, workers in CRAWL_QUEUE:
        if slots <= 0 or _stop:
            break
        if code in running:
            continue
        if should_skip(code, st):
            continue
        before = feed_count(code)
        try:
            start_crawl(code, delay, workers)
            st.setdefault("started", {})[code] = {
                "before": before,
                "ts": time.time(),
            }
            slots -= 1
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            print(f"START_ERR {code}: {exc} — continue", flush=True)
            mark_fail(st, code)


def reap_finished(st: dict) -> None:
    running = running_crawl_codes()
    started = st.get("started") or {}
    done_codes = [c for c in list(started) if c not in running]
    for code in done_codes:
        info = started.pop(code, {})
        before = int(info.get("before") or 0)
        after = feed_count(code)
        if after > before or after >= SKIP_IF_AT_LEAST.get(code, 10**18):
            print(f"DONE {code} {before}->{after}", flush=True)
            mark_ok(st, code)
        else:
            # no growth — treat as soft fail / blocked
            print(f"STALL {code} {before}->{after}", flush=True)
            mark_fail(st, code)
    st["started"] = started


def maybe_ingest(st: dict) -> None:
    if ingest_running():
        return
    total = total_feeds()
    last = int(st.get("last_ingest_feed_total") or 0)
    # Prefer FLO if feed >> db gap; otherwise rotate grown merchants
    flo = feed_count("flo")
    # Always try FLO once if feed large (ingest script is idempotent-ish)
    if flo >= 10_000 and (total - last >= INGEST_AFTER_GROWTH or last == 0):
        # ingest high-churn merchants first
        grown = []
        for code, _, _ in CRAWL_QUEUE:
            if feed_count(code) > 0:
                grown.append(code)
        # FLO alone is heaviest — if FLO still likely incomplete in DB, prioritize it
        merchants = "flo"
        if last > 0 and total - last < 5000:
            # incremental: non-flo bundle
            merchants = ",".join(
                c for c in grown if c != "flo" and feed_count(c) < SKIP_IF_AT_LEAST.get(c, 0) + 1
            ) or "trendyol,mediamarkt,civil,network,n11"
        try:
            start_ingest(merchants)
            st["last_ingest_feed_total"] = total
        except Exception as exc:  # noqa: BLE001
            print(f"INGEST_ERR: {exc} — continue", flush=True)


def main() -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    st = load_state()
    print(
        f"auto_partner_ops start total={total_feeds()}/{TARGET} "
        f"parallel={MAX_PARALLEL_CRAWLS} complete_only={COMPLETE_ONLY}",
        flush=True,
    )
    # Kill old sequential supervisor so we don't double-schedule
    for pat in (
        "supervise_partner_crawls.py",
        "scripts/auto_partner_ops.py",
    ):
        # don't kill ourselves
        pass

    while not _stop:
        try:
            if total_feeds() >= TARGET:
                print(f"TARGET REACHED {total_feeds()} — stop crawls, keep ingest/backfill once", flush=True)
                if not ingest_running():
                    maybe_ingest(st)
                start_backfill_if_idle()
                save_state(st)
                break

            reap_finished(st)
            fill_crawl_slots(st)
            maybe_ingest(st)
            # periodic image backfill when crawl slots full or idle ingest
            if len(running_crawl_codes()) >= MAX_PARALLEL_CRAWLS or not ingest_running():
                start_backfill_if_idle()

            st["round"] = int(st.get("round") or 0) + 1
            if st["round"] % 6 == 0:
                print(
                    f"heartbeat total={total_feeds()}/{TARGET} "
                    f"running={sorted(running_crawl_codes())} "
                    f"ingest={ingest_running()}",
                    flush=True,
                )
            save_state(st)
        except Exception as exc:  # noqa: BLE001
            print(f"LOOP_ERR: {exc} — continue", flush=True)
        time.sleep(30)

    print(f"auto_partner_ops exit total={total_feeds()}", flush=True)


if __name__ == "__main__":
    main()
