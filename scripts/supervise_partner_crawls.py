#!/usr/bin/env python3
"""Sequential durable partner crawl supervisor (ADR-010).

Runs merchants one-by-one (or small parallel groups) so macOS does not OOM-kill
multi-million sitemap workers. Resumes from feed checkpoints. Never invents data.

  nohup .venv-crawl/bin/python -u scripts/supervise_partner_crawls.py > /tmp/supervise-partners.log 2>&1 &
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv-crawl" / "bin" / "python"
LOG = Path("/tmp/taksitlio-partner-crawls")
FEED = ROOT / "crawler" / "feeds" / "live"

# (merchant, delay, workers, expected_min_hint)
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


def run_one(code: str, delay: float, workers: int) -> int:
    LOG.mkdir(parents=True, exist_ok=True)
    log = LOG / f"supervise-{code}.log"
    before = feed_count(code)
    print(f"\n=== START {code} before={before} workers={workers} ===", flush=True)
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
            ],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
        )
        rc = proc.wait()
    after = feed_count(code)
    print(f"=== DONE {code} rc={rc} {before}->{after} ===", flush=True)
    return rc


def main() -> None:
    # Kill competing parallel crawls except we are the supervisor
    print("supervisor starting", flush=True)
    rounds = 0
    while rounds < 3:
        rounds += 1
        print(f"\n##### ROUND {rounds} #####", flush=True)
        for code, delay, workers in QUEUE:
            run_one(code, delay, workers)
            time.sleep(2)
        # crawl4ai leftovers
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
            )
        total = sum(feed_count(c) for c, _, _ in QUEUE) + feed_count("vatan") + feed_count(
            "hepsiburada"
        ) + feed_count("vivense") + feed_count("koctas")
        print(f"ROUND {rounds} approx total products={total}", flush=True)
    print("supervisor finished 3 rounds", flush=True)


if __name__ == "__main__":
    # Ensure only one supervisor
    main()
