#!/usr/bin/env python3
"""Write URLFrontier-oriented seed plan from crawl-registry.yaml.

URLFrontier gRPC inject is ops-specific; this emits the seed JSON that the
StormCrawler topology expects as URL metadata (taksitlio.* keys).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bind_crawl_feeds import inject_seeds_plan, load_registry  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--registry",
        type=Path,
        default=ROOT / "crawler/ops/crawl-registry.yaml",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=ROOT / "crawler/ops/seed-plan.json",
    )
    args = p.parse_args()
    registry = load_registry(args.registry)
    seeds = inject_seeds_plan(registry)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"count": len(seeds), "seeds": seeds}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out} ({len(seeds)} seeds)")


if __name__ == "__main__":
    main()
