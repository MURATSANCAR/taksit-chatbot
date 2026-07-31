"""ADR-010 §80 metrics snapshot scaffold.

Reports code/gate readiness + optional live DB counts.
Does not invent merchant/product coverage; missing live = 0 / null.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class GateStatus:
    name: str
    status: str
    note: str = ""


@dataclass
class MetricsSnapshot:
    generated_at: str
    environment: str
    gates: list[GateStatus] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def baseline_gates() -> list[GateStatus]:
    return [
        GateStatus("Data Ingestion", "CODE_READY", "P15; live authorized feed pending"),
        GateStatus("Data Quality", "CODE_READY", "P6 scorer; live coverage unmeasured"),
        GateStatus("Fast Product Path", "CODE_READY", "P14; needs catalog products"),
        GateStatus("Finance Mapping", "CODE_READY", "P12; needs real rate/campaign rows"),
        GateStatus("Recommendation", "CODE_READY", "P4 ranking; live accuracy unmeasured"),
        GateStatus("Campaign (personal approval)", "CLOSED", "ADR-009 provisional required"),
        GateStatus("Media / CDN", "OPS_PARTIAL", "MinIO wired; public CDN DNS optional"),
        GateStatus("FAST LoRA", "SCAFFOLD", "P17 export/stub; GPU train pending"),
        GateStatus("Quality (ADR-009 HR100)", "REJECT", "generic FAST A/B/C failed bar"),
        GateStatus("Runtime / Provisional", "BLOCKED", "live measurement incomplete"),
    ]


def empty_counts() -> dict[str, Any]:
    keys = (
        "merchants",
        "ingestion_sources",
        "products",
        "canonical_products",
        "offers",
        "institutions",
        "campaigns",
        "product_finance_options",
        "media_assets",
    )
    return {k: None for k in keys}


def empty_coverage() -> dict[str, Any]:
    return {
        "primary_image_coverage": None,
        "fresh_price_coverage": None,
        "stock_coverage": None,
        "finance_option_coverage": None,
        "payment_plan_coverage": None,
        "note": "Populate only from live SQL after authorized ingest",
    }


def empty_latency() -> dict[str, Any]:
    return {
        "entity_resolution_p95_ms": None,
        "product_search_p95_ms": None,
        "first_card_p95_ms": None,
        "full_result_p95_ms": None,
        "note": "Run live benchmark; do not invent values",
    }


async def fetch_live_counts(database_url: str) -> dict[str, Any]:
    import asyncpg

    counts = empty_counts()
    conn = await asyncpg.connect(database_url)
    try:
        mapping = {
            "merchants": "SELECT count(*) FROM merchants",
            "ingestion_sources": "SELECT count(*) FROM ingestion_sources",
            "products": "SELECT count(*) FROM products",
            "canonical_products": "SELECT count(*) FROM canonical_products",
            "offers": "SELECT count(*) FROM product_offers",
            "institutions": "SELECT count(*) FROM financial_institutions",
            "campaigns": "SELECT count(*) FROM finance_campaigns",
            "product_finance_options": "SELECT count(*) FROM product_finance_options",
            "media_assets": "SELECT count(*) FROM media_assets",
        }
        for key, sql in mapping.items():
            try:
                counts[key] = int(await conn.fetchval(sql))
            except Exception as exc:  # noqa: BLE001
                counts[key] = {"error": type(exc).__name__}
    finally:
        await conn.close()
    return counts


def build_snapshot(
    *,
    environment: str,
    counts: Optional[dict[str, Any]] = None,
) -> MetricsSnapshot:
    blockers = [
        "Authorized merchant feed/API not bound in production",
        "ADR-009 provisional not locked; Campaign Gate CLOSED",
        "FAST HR100 quality still REJECT without task-specific LoRA deploy",
        "Live search/finance latency & coverage not yet measured",
    ]
    return MetricsSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        environment=environment,
        gates=baseline_gates(),
        counts=counts or empty_counts(),
        coverage=empty_coverage(),
        latency=empty_latency(),
        blockers=blockers,
        notes=[
            "Skeleton ADR-010 P0–P17 code-ready; live data gates open",
            "No scraper; only authorized feeds/adapters",
            "MinIO may be configured via OBJECT_STORAGE_BACKEND=s3",
        ],
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-010 §80 metrics scaffold")
    parser.add_argument("--env-name", default=os.environ.get("TAKSITLIO_ENV", "unknown"))
    parser.add_argument("--live-db", action="store_true", help="Query DATABASE_URL counts")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("evaluation/reports/adr010-metrics-scaffold.json"),
    )
    args = parser.parse_args(argv)

    counts = empty_counts()
    if args.live_db:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("DATABASE_URL required for --live-db")
        import asyncio

        counts = asyncio.run(fetch_live_counts(url))

    snap = build_snapshot(environment=args.env_name, counts=counts)
    payload = asdict(snap)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps({"gates": {g.name: g.status for g in snap.gates}, "blockers": snap.blockers}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
