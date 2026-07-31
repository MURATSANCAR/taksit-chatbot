#!/usr/bin/env python3
"""Sequential durable partner crawl supervisor (ADR-010).

Hard stop at TARGET_PRODUCTS (default 1_000_000) across crawler/feeds/live.

  nohup .venv-crawl/bin/python -u scripts/supervise_partner_crawls.py > /tmp/supervise-partners.log 2>&1 &
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv-crawl" / "bin" / "python"
LOG = Path("/tmp/taksitlio-partner-crawls")
FEED = ROOT / "crawler" / "feeds" / "live"

TARGET_PRODUCTS = int(os.environ.get("CRAWL_GLOBAL_PRODUCT_CAP", "1000000"))

QUEUE: list[tuple[str, float, int]] = [
    ("flo", 0.12, 6),
    ("teknosa", 0.12, 6),
    ("n11", 0.15, 4),
    ("dr", 0.12, 8),
    ("mediamarkt", 0.12, 8),
    ("trendyol", 0.3, 3),
    ("civil", 0.15, 4),
    ("network", 0.15, 4),
    ("arcelik", 0.2, 3),
    ("beko", 0.2, 3),
    ("decathlon", 0.5, 2),
    ("evofone", 0.2, 3),
]


def feed_count(code: str) -> int:
    path = FEED / f"src-m-{code}.json"
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("count") or 0)
    except Exception:
        return 0


def total_live_products() -> int:
    total = 0
    for path in FEED.glob("src-m-*.json"):
        try:
            total += int(json.loads(path.read_text(encoding="utf-8")).get("count") or 0)
        except Exception:
            continue
    return total


def hit_target() -> bool:
    total = total_live_products()
    if total >= TARGET_PRODUCTS:
        print(f"TARGET REACHED: {total} >= {TARGET_PRODUCTS} — stopping", flush=True)
        return True
    return False


def run_one(code: str, delay: float, workers: int) -> int:
    if hit_target():
        return 0
    LOG.mkdir(parents=True, exist_ok=True)
    log = LOG / f"supervise-{code}.log"
    before = feed_count(code)
    print(
        f"\n=== START {code} before={before} workers={workers} "
        f"total={total_live_products()}/{TARGET_PRODUCTS} ===",
        flush=True,
    )
    env = os.environ.copy()
    env["CRAWL_GLOBAL_PRODUCT_CAP"] = str(TARGET_PRODUCTS)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- supervise start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        proc = subprocess.Popen(
            [
                str(PY),
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
                str(TARGET_PRODUCTS),
            ],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
        )
        rc = proc.wait()
    after = feed_count(code)
    print(
        f"=== DONE {code} rc={rc} {before}->{after} "
        f"total={total_live_products()}/{TARGET_PRODUCTS} ===",
        flush=True,
    )
    return rc


def main() -> None:
    print(
        f"supervisor starting TARGET={TARGET_PRODUCTS} "
        f"current={total_live_products()}",
        flush=True,
    )
    if hit_target():
        return

    # Wait for any in-flight FLO crawl (memory-safe single runner)
    while True:
        r = subprocess.run(
            ["pgrep", "-f", "fetch_live_merchant_feeds.py --merchants flo"],
            capture_output=True,
        )
        if r.returncode != 0:
            break
        print(f"  waiting FLO... total={total_live_products()}", flush=True)
        if hit_target():
            return
        time.sleep(120)

    rounds = 0
    while rounds < 3:
        rounds += 1
        if hit_target():
            break
        print(f"\n##### ROUND {rounds} #####", flush=True)
        for code, delay, workers in QUEUE:
            if hit_target():
                break
            # Skip FLO if already near-complete from prior run
            if code == "flo" and feed_count("flo") >= 200000:
                print("skip flo (already large)", flush=True)
                continue
            run_one(code, delay, workers)
            time.sleep(2)
        if hit_target():
            break
        log = LOG / "supervise-c4ai.log"
        with log.open("a", encoding="utf-8") as fh:
            subprocess.run(
                [
                    str(PY),
                    "-u",
                    str(ROOT / "scripts" / "fetch_via_crawl4ai.py"),
                    "--merchants",
                    "hepsiburada,vivense,koctas",
                    "--delay",
                    "0.5",
                    "--limit",
                    "0",
                ],
                cwd=str(ROOT),
                stdout=fh,
                stderr=subprocess.STDOUT,
                env={**os.environ, "CRAWL_GLOBAL_PRODUCT_CAP": str(TARGET_PRODUCTS)},
            )
        print(
            f"ROUND {rounds} total products={total_live_products()}/{TARGET_PRODUCTS}",
            flush=True,
        )
    print(
        f"supervisor finished total={total_live_products()}/{TARGET_PRODUCTS}",
        flush=True,
    )


if __name__ == "__main__":
    main()
