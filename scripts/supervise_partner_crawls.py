#!/usr/bin/env python3
"""Sequential durable partner crawl supervisor (ADR-010).

Hard stop at TARGET_PRODUCTS (default 1_000_000) across crawler/feeds/live.
Prioritizes high-yield / faster merchants; slow ones (n11, decathlon) last.

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

# Fast / high-yield first; n11 & decathlon last (slow / WAF).
QUEUE: list[tuple[str, float, int]] = [
    ("trendyol", 0.25, 4),
    ("mediamarkt", 0.1, 10),
    ("dr", 0.1, 10),
    ("civil", 0.12, 6),
    ("network", 0.12, 6),
    ("evofone", 0.15, 4),
    ("teknosa", 0.12, 6),
    ("flo", 0.12, 6),
    ("arcelik", 0.2, 3),
    ("beko", 0.2, 3),
    ("decathlon", 0.5, 2),
    ("n11", 0.15, 4),  # huge but slow — last
]

# Skip merchant if feed already at/above this count (resume later rounds if needed).
SKIP_IF_AT_LEAST: dict[str, int] = {
    "flo": 150_000,
    "teknosa": 7_000,
    "network": 6_000,
    "arcelik": 1_000,
    "beko": 1_000,
    "evofone": 100,
    "vatan": 900,
    "mediamarkt": 2_000,  # allow growth until 2k then skip
    "dr": 2_000,
    "trendyol": 50_000,  # keep growing TY hard
    "civil": 500,
    "n11": 10**12,  # always skip in normal rounds unless FORCE_N11=1
    "decathlon": 10**12 if os.environ.get("FORCE_DECATHLON") != "1" else 0,
}

FORCE_N11 = os.environ.get("FORCE_N11", "").strip() in {"1", "true", "yes"}


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


def should_skip(code: str) -> bool:
    if code == "n11" and not FORCE_N11:
        print(f"skip {code} (deferred — set FORCE_N11=1 to run)", flush=True)
        return True
    if code == "decathlon" and os.environ.get("FORCE_DECATHLON") != "1":
        print(f"skip {code} (WAF — set FORCE_DECATHLON=1 to retry)", flush=True)
        return True
    thresh = SKIP_IF_AT_LEAST.get(code)
    if thresh is not None and feed_count(code) >= thresh:
        print(f"skip {code} (already {feed_count(code)} >= {thresh})", flush=True)
        return True
    return False


def run_one(code: str, delay: float, workers: int) -> int:
    if hit_target() or should_skip(code):
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
        f"current={total_live_products()} FORCE_N11={FORCE_N11}",
        flush=True,
    )
    if hit_target():
        return

    rounds = 0
    while rounds < 3:
        rounds += 1
        if hit_target():
            break
        print(f"\n##### ROUND {rounds} #####", flush=True)
        for code, delay, workers in QUEUE:
            if hit_target():
                break
            run_one(code, delay, workers)
            time.sleep(2)
        if hit_target():
            break
        log = LOG / "supervise-c4ai.log"
        print("=== crawl4ai hepsiburada,vivense,koctas ===", flush=True)
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
