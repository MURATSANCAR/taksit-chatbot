"""P9 — queue handlers + media enqueue."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from taksitlio.ingestion_scheduler.domain import (
    PRIORITY_DEFAULT,
    SchedulerJobSpec,
    SchedulerQueue,
)
from taksitlio.ingestion_scheduler.handlers import HandlerContext, QueueDispatchHandler
from taksitlio.ingestion_scheduler.media_enqueue import enqueue_media_jobs_for_applied
from taksitlio.ingestion_scheduler.repository import InMemorySchedulerJobRepository
from taksitlio.ingestion_scheduler.worker import LeaseLoopWorker
from taksitlio.media.storage import LocalObjectStorage
from taksitlio.product.catalog import InMemoryProductCatalogRepository
from taksitlio.product.upsert import plan_product_upsert
from taksitlio.ingestion.protocol import NormalizedProduct


def _png_bytes(size: int = 64) -> bytes:
    img = Image.new("RGB", (size, size), color=(20, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_media_fetch_attaches_cdn(tmp_path: Path) -> None:
    catalog = InMemoryProductCatalogRepository()
    product = await catalog.upsert_product(
        merchant_id=1,
        plan=plan_product_upsert(
            NormalizedProduct(
                external_product_id="SKU-1",
                display_name="Phone",
            )
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    storage = LocalObjectStorage(tmp_path / "media", cdn_base_url="https://cdn.test")
    png = _png_bytes()

    async def fake_download(_url: str, **_kwargs):  # noqa: ANN001
        return png

    handler = QueueDispatchHandler(
        HandlerContext(catalog=catalog, storage=storage, download_image=fake_download)
    )
    repo = InMemorySchedulerJobRepository()
    await repo.enqueue(
        SchedulerJobSpec(
            queue_name=SchedulerQueue.MEDIA_FETCH,
            priority=PRIORITY_DEFAULT,
            product_id=str(product.id),
            external_item_id="SKU-1",
            payload={"source_url": "https://merchant.example/img.png"},
        )
    )
    worker = LeaseLoopWorker(repo=repo, worker_id="w", handler=handler)
    await worker.tick()
    stored = await catalog.get_product(product.id)
    assert stored is not None
    assert stored.primary_cdn_url is not None
    assert stored.primary_cdn_url.startswith("https://cdn.test/")
    assert "merchant.example" not in stored.primary_cdn_url


@pytest.mark.asyncio
async def test_price_refresh_and_stale() -> None:
    from taksitlio.ingestion.protocol import NormalizedOffer
    from taksitlio.product.upsert import plan_offer_upsert

    catalog = InMemoryProductCatalogRepository()
    product = await catalog.upsert_product(
        merchant_id=1,
        plan=plan_product_upsert(
            NormalizedProduct(external_product_id="S", display_name="X")
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=1,
        product_id=product.id,
        plan=plan_offer_upsert(
            NormalizedOffer(external_product_id="S", current_price=100.0)
        ),
    )
    handler = QueueDispatchHandler(HandlerContext(catalog=catalog))
    repo = InMemorySchedulerJobRepository()
    await repo.enqueue(
        SchedulerJobSpec(
            queue_name=SchedulerQueue.PRICE_REFRESH,
            priority=10,
            product_id=str(product.id),
            payload={},
        )
    )
    worker = LeaseLoopWorker(repo=repo, worker_id="w", handler=handler)
    await worker.tick()
    offer = (await catalog.get_offer_hash(product_id=product.id))
    # hash unchanged but freshness stale — check via internal
    assert catalog._offers[product.id].freshness_status == "STALE"

    await repo.enqueue(
        SchedulerJobSpec(
            queue_name=SchedulerQueue.PRICE_REFRESH,
            priority=10,
            product_id=str(product.id),
            external_item_id="S-refresh",
            payload={"current_price": 90.0, "stock_status": "AVAILABLE"},
        )
    )
    await worker.tick()
    assert catalog._offers[product.id].current_price == 90.0
    assert catalog._offers[product.id].freshness_status == "FRESH"
    _ = offer


@pytest.mark.asyncio
async def test_enqueue_media_from_apply(tmp_path: Path) -> None:
    import json

    from taksitlio.ingestion.binding import SourceBinding
    from taksitlio.ingestion.runner import run_ingestion_dry
    from taksitlio.product.catalog import apply_ingestion_to_catalog

    feed = tmp_path / "f.json"
    feed.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "P1",
                        "name": "Cam",
                        "price": 10,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                        "image_url": "https://merchant.example/a.jpg",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = await run_ingestion_dry(
        SourceBinding(
            source_code="s",
            adapter_code="generic.json_feed.v1",
            merchant_id="m",
            config={"feed_path": str(feed)},
        )
    )
    catalog = InMemoryProductCatalogRepository()
    applied = await apply_ingestion_to_catalog(
        result, merchant_id=1, catalog=catalog
    )
    scheduler = InMemorySchedulerJobRepository()
    n = await enqueue_media_jobs_for_applied(
        ingestion=result,
        applied=applied,
        scheduler=scheduler,
        catalog=catalog,
    )
    assert n == 1
    jobs = await scheduler.list_jobs()
    assert jobs[0].queue_name == "MEDIA_FETCH"
    assert "merchant.example" in jobs[0].payload["source_url"]
    product = await catalog.get_product(applied.items[0].product_id)  # type: ignore[arg-type]
    assert product is not None
    assert product.pending_source_image_url is not None
