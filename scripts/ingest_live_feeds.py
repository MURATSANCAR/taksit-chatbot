#!/usr/bin/env python3
"""Ingest crawler/feeds/live/*.json into ADR-010 catalog (dry-run + apply).

Uses opaque merchant codes from crawl-registry. Never invents prices/rates.
Writes a local catalog snapshot for operator verification.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIVE = ROOT / "crawler" / "feeds" / "live"


MERCHANT_FEEDS = {
    "src-m-vatan": {
        "merchant_code": "m-vatan",
        "display_name": "Vatan Bilgisayar",
        "merchant_id": 101,
    },
    "src-m-mediamarkt": {
        "merchant_code": "m-mediamarkt",
        "display_name": "MediaMarkt",
        "merchant_id": 102,
    },
}


async def ingest_product_feed(
    *,
    source_code: str,
    feed_path: Path,
    merchant_id: int,
    catalog,
) -> dict[str, Any]:
    from taksitlio.ingestion.binding import SourceBinding
    from taksitlio.ingestion.runner import run_ingestion_dry
    from taksitlio.product.catalog import apply_ingestion_to_catalog

    binding = SourceBinding(
        source_code=source_code,
        adapter_code="generic.json_feed.v1",
        merchant_id=str(merchant_id),
        config={"feed_path": str(feed_path), "source_reference": str(feed_path)},
    )
    result = await run_ingestion_dry(binding, limit=500)
    applied = await apply_ingestion_to_catalog(
        result,
        catalog=catalog,
        merchant_id=merchant_id,
    )
    return {
        "source_code": source_code,
        "discovered": result.discovered,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "quarantined": result.quarantined,
        "chatbot_visible": result.chatbot_visible,
        "upserted_products": applied.upserted_products,
        "upserted_offers": applied.upserted_offers,
        "skipped_quarantined": applied.skipped_quarantined,
    }


async def ingest_campaign_feed(feed_path: Path, institution_code: str) -> dict[str, Any]:
    from taksitlio.campaign_catalog.feed_apply import (
        InMemoryCampaignCatalog,
        apply_campaign_feed_result,
    )
    from taksitlio.ingestion.adapters.generic_campaign_feed import (
        GenericCampaignFeedAdapter,
        run_campaign_feed_dry,
    )

    adapter = GenericCampaignFeedAdapter(
        feed_path=feed_path,
        default_institution_code=institution_code,
        source_reference=str(feed_path),
    )
    result = await run_campaign_feed_dry(adapter)
    catalog = InMemoryCampaignCatalog()
    applied = apply_campaign_feed_result(catalog, result)
    return {
        "source": str(feed_path.name),
        "campaigns": len(result.campaigns),
        "rates": len(result.rates),
        "rates_skipped_no_explicit_rate": result.rates_skipped_no_explicit_rate,
        "applied": applied,
        "campaign_codes": [c.campaign_code for c in result.campaigns],
    }


async def main_async(args: argparse.Namespace) -> int:
    from taksitlio.product.catalog import InMemoryProductCatalogRepository

    catalog = InMemoryProductCatalogRepository()
    reports: list[dict[str, Any]] = []

    for source_code, meta in MERCHANT_FEEDS.items():
        path = LIVE / f"{source_code}.json"
        if not path.exists():
            reports.append({"source_code": source_code, "skipped": "missing feed"})
            continue
        reports.append(
            await ingest_product_feed(
                source_code=source_code,
                feed_path=path,
                merchant_id=int(meta["merchant_id"]),
                catalog=catalog,
            )
        )

    campaign_reports = []
    for path in sorted(LIVE.glob("src-b-*.json")):
        # institution from filename src-b-fibabanka -> fi-fibabanka heuristic for ops files
        code = path.stem.replace("src-b-", "fi-", 1)
        campaign_reports.append(await ingest_campaign_feed(path, code))

    # Snapshot products/offers currently in memory catalog
    products = []
    offers = []
    # InMemoryProductCatalogRepository internals
    for key, prod in getattr(catalog, "_products", {}).items():
        products.append(
            {
                "merchant_id": prod.merchant_id,
                "external_product_id": prod.external_product_id,
                "display_name": prod.display_name,
                "data_quality_status": prod.data_quality_status,
                "status": prod.status,
                "attributes": dict(prod.attributes or {}),
            }
        )
    for key, offer in getattr(catalog, "_offers", {}).items():
        offers.append(
            {
                "product_id": offer.product_id,
                "merchant_id": offer.merchant_id,
                "current_price": offer.current_price,
                "currency": offer.currency,
                "stock_status": offer.stock_status,
                "freshness_status": offer.freshness_status,
            }
        )

    snapshot = {
        "product_ingest": reports,
        "campaign_ingest": campaign_reports,
        "catalog": {
            "product_count": len(products),
            "offer_count": len(offers),
            "products": products,
            "offers": offers,
        },
    }
    out = LIVE / "ingest_snapshot.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "product_ingest": reports,
        "campaign_ingest": campaign_reports,
        "catalog_product_count": len(products),
        "catalog_offer_count": len(offers),
        "snapshot": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
