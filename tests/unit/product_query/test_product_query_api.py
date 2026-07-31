"""HTTP resolve-entities + popular-cache hit path."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container


@pytest.mark.asyncio
async def test_resolve_entities_endpoint() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/product-query/resolve-entities",
            json={
                "entity_type": "merchant",
                "texts": ["exmple merchant"],
                "candidates": [
                    {
                        "entity_id": "m1",
                        "display_name": "Example Merchant",
                        "canonical_name": "Example Merchant",
                        "aliases": ["exmple merchant"],
                        "entity_type": "merchant",
                    }
                ],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolutions"][0]["action"] == "AUTO_SELECT"
    assert body["resolutions"][0]["resolved_entity_id"] == "m1"
    await container.aclose()


@pytest.mark.asyncio
async def test_search_popular_cache_hit() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    product = {
        "product_id": "p1",
        "display_name": "Laptop",
        "merchant_id": "m1",
        "merchant_display_name": "M",
        "price": 10000,
        "stock_status": "AVAILABLE",
        "price_freshness": "FRESH",
        "has_primary_image": True,
        "finance_active": True,
        "rate_fresh": True,
        "best_monthly_payment": 900,
        "best_total_repayment": 10800,
        "query_relevance": 0.9,
        "attribute_coverage": 0.8,
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/product-query/search",
            json={
                "utterance": "laptop",
                "products": [product],
                "cache_version": "v-test",
            },
        )
        assert first.status_code == 200
        assert first.json()["diagnostics"].get("cache_hit") is None
        assert first.json()["cards"]

        second = await client.post(
            "/v1/product-query/search",
            json={
                "utterance": "laptop",
                "products": [],  # would yield empty if not cached
                "cache_version": "v-test",
            },
        )
    assert second.status_code == 200
    assert second.json()["diagnostics"].get("cache_hit") == "popular"
    assert second.json()["cards"]
    await container.aclose()
