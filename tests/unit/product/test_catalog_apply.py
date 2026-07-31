"""P8 — catalog apply from ingestion dry-run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taksitlio.ingestion.binding import SourceBinding
from taksitlio.ingestion.runner import run_ingestion_dry
from taksitlio.product.catalog import (
    InMemoryProductCatalogRepository,
    apply_ingestion_to_catalog,
)


@pytest.mark.asyncio
async def test_apply_upserts_visible_skips_quarantine(tmp_path: Path) -> None:
    feed = tmp_path / "feed.json"
    feed.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "OK-1",
                        "name": "Laptop Pro",
                        "price": 20000,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                        "brand": "Acme",
                        "model": "LP-1",
                    },
                    {
                        "id": "BAD-1",
                        "name": "No Price",
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    result = await run_ingestion_dry(
        SourceBinding(
            source_code="s1",
            adapter_code="generic.json_feed.v1",
            merchant_id="m",
            config={"feed_path": str(feed)},
        )
    )
    catalog = InMemoryProductCatalogRepository()
    applied = await apply_ingestion_to_catalog(
        result, merchant_id=42, catalog=catalog, only_chatbot_visible=True
    )
    assert applied.upserted_products == 1
    assert applied.upserted_offers == 1
    assert applied.skipped_quarantined >= 1
    products = await catalog.list_products(merchant_id=42)
    assert len(products) == 1
    assert products[0].external_product_id == "OK-1"

    # Second apply should count unchanged
    applied2 = await apply_ingestion_to_catalog(
        result, merchant_id=42, catalog=catalog, only_chatbot_visible=True
    )
    assert applied2.skipped_unchanged == 1


@pytest.mark.asyncio
async def test_daemon_runs_finite_ticks() -> None:
    from taksitlio.ingestion_scheduler.daemon import run_daemon
    from taksitlio.ingestion_scheduler.domain import SchedulerJobSpec, SchedulerQueue
    from taksitlio.ingestion_scheduler.repository import InMemorySchedulerJobRepository

    repo = InMemorySchedulerJobRepository()
    await repo.enqueue(
        SchedulerJobSpec(
            queue_name=SchedulerQueue.PRICE_REFRESH,
            priority=10,
            external_item_id="x",
        )
    )
    ticks = await run_daemon(
        repo, worker_id="d1", poll_interval_seconds=0.01, max_ticks=3
    )
    assert ticks == 3
    jobs = await repo.list_jobs()
    assert jobs[0].status == "SUCCEEDED"
