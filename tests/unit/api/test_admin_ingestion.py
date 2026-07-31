"""Admin ingestion / data-quality HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container


@pytest.mark.asyncio
async def test_list_adapters_and_score() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        adapters = await client.get("/v1/admin/ingestion/adapters")
        assert adapters.status_code == 200
        assert "generic.json_feed.v1" in adapters.json()["adapters"]

        scored = await client.post(
            "/v1/admin/data-quality/score",
            json={
                "external_product_id": "1",
                "display_name": "Phone",
                "price": 1000,
                "currency": "TRY",
                "stock_status": "AVAILABLE",
                "has_primary_image": True,
                "image_cdn_ready": True,
                "source_reference": "src",
                "price_fresh": True,
            },
        )
    assert scored.status_code == 200
    assert scored.json()["status"] == "READY"
    await container.aclose()


@pytest.mark.asyncio
async def test_dry_run_endpoint(tmp_path: Path) -> None:
    feed = tmp_path / "f.json"
    feed.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "A1",
                        "name": "Item",
                        "price": 99.0,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/admin/ingestion/dry-run",
            json={
                "source_code": "op-src-1",
                "adapter_code": "generic.json_feed.v1",
                "merchant_id": "m-opaque",
                "config": {"feed_path": str(feed)},
            },
        )
        forbidden = await client.post(
            "/v1/admin/ingestion/dry-run",
            json={
                "source_code": "op-src-2",
                "adapter_code": "generic.json_feed.v1",
                "merchant_id": "m-opaque",
                "config": {"feed_path": str(feed), "api_key": "secret"},
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["discovered"] == 1
    assert body["health"]["status"] in {"HEALTHY", "DEGRADED"}
    assert forbidden.status_code == 400
    await container.aclose()


@pytest.mark.asyncio
async def test_enqueue_and_tick() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        enq = await client.post(
            "/v1/admin/ingestion/scheduler/enqueue",
            json={
                "queue_name": "PRICE_REFRESH",
                "priority": 10,
                "external_item_id": "sku-9",
            },
        )
        assert enq.status_code == 200
        tick = await client.post(
            "/v1/admin/ingestion/scheduler/tick",
            json={"worker_id": "t1"},
        )
        empty = await client.post(
            "/v1/admin/ingestion/scheduler/tick",
            json={"worker_id": "t1"},
        )
    assert tick.status_code == 200
    assert tick.json()["leased"] is True
    assert empty.json()["leased"] is False
    await container.aclose()


@pytest.mark.asyncio
async def test_dry_run_persist(tmp_path: Path) -> None:
    feed = tmp_path / "f.json"
    feed.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "A1",
                        "name": "Item",
                        "price": 99.0,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/admin/ingestion/dry-run/persist",
            json={
                "source_code": "op-src-persist",
                "adapter_code": "generic.json_feed.v1",
                "merchant_id": "opaque-m",
                "merchant_id_int": 1,
                "config": {"feed_path": str(feed)},
                "persist": True,
                "enqueue_discovery": True,
                "upsert_products": True,
            },
        )
        health = await client.get("/v1/admin/ingestion/sources/health")
        runs = await client.get("/v1/admin/ingestion/runs")
        products = await client.get("/v1/admin/products?merchant_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted_run_id"] is not None
    assert body["enqueued_job_id"] is not None
    assert body["catalog"] is not None
    assert body["catalog"]["upserted_products"] >= 1
    assert health.json()["mode"] == "repository"
    assert health.json()["sources"]
    assert runs.json()["runs"]
    assert products.json()["products"]
    await container.aclose()
