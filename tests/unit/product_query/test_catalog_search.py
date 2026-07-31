"""P10 — catalog → search candidates."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container
from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
from taksitlio.product.upsert import plan_offer_upsert, plan_product_upsert
from taksitlio.product_query.candidates import (
    load_search_candidates_from_catalog,
    product_to_search_candidate,
)
from taksitlio.product_query.ranking import RankingMode, RankableProduct, rank_products


def _available_offer(external_id: str, price: float) -> object:
    return plan_offer_upsert(
        NormalizedOffer(
            external_product_id=external_id,
            current_price=price,
            currency="TRY",
        ),
        NormalizedStock(
            external_product_id=external_id,
            stock_status="AVAILABLE",
        ),
    )


@pytest.mark.asyncio
async def test_load_candidates_and_cheapest_rank() -> None:
    container = build_in_memory_container()
    catalog = container.extras["product_catalog"]
    p = await catalog.upsert_product(
        merchant_id=7,
        plan=plan_product_upsert(
            NormalizedProduct(external_product_id="L1", display_name="Laptop Pro 16")
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=7,
        product_id=p.id,
        plan=_available_offer("L1", 25000),
    )
    await catalog.attach_primary_media(
        p.id,
        cdn_url="https://cdn.test/a.webp",
        sha256="abc",
        status="READY",
        source_url="https://merchant.example/a.jpg",
    )
    p2 = await catalog.upsert_product(
        merchant_id=7,
        plan=plan_product_upsert(
            NormalizedProduct(external_product_id="L2", display_name="Laptop Air")
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=7,
        product_id=p2.id,
        plan=_available_offer("L2", 18000),
    )

    cands = await load_search_candidates_from_catalog(
        catalog, utterance="laptop istiyorum", merchant_id=7
    )
    assert len(cands) == 2
    assert all(c.merchant_display_name.startswith("merchant:") for c in cands)

    ranked = rank_products(
        [
            RankableProduct(
                product_id=c.product_id,
                price=c.price,
                stock_status=c.stock_status,
                price_freshness=c.price_freshness,
                has_primary_image=c.has_primary_image,
                query_relevance=c.query_relevance,
                attribute_coverage=c.attribute_coverage,
                finance_active=c.finance_active,
                rate_fresh=c.rate_fresh,
            )
            for c in cands
        ],
        mode=RankingMode.CHEAPEST_PRODUCT_PRICE,
    )
    assert ranked[0].product_id == str(p2.id)
    await container.aclose()


@pytest.mark.asyncio
async def test_search_uses_catalog_when_products_empty() -> None:
    container = build_in_memory_container()
    catalog = container.extras["product_catalog"]
    p = await catalog.upsert_product(
        merchant_id=1,
        plan=plan_product_upsert(
            NormalizedProduct(external_product_id="T1", display_name="Tablet X")
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=1,
        product_id=p.id,
        plan=_available_offer("T1", 9000),
    )
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/product-query/search",
            json={
                "utterance": "tablet",
                "ranking_mode": "CHEAPEST_PRODUCT_PRICE",
                "use_catalog": True,
                "use_popular_cache": False,
                "catalog_merchant_id": 1,
                "products": [],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["diagnostics"]["candidate_source"] == "catalog"
    assert body["cards"]
    assert body["cards"][0]["product_id"] == str(p.id)
    await container.aclose()


@pytest.mark.asyncio
async def test_quarantined_skipped() -> None:
    from taksitlio.product.catalog import StoredOffer, StoredProduct

    product = StoredProduct(
        id=1,
        merchant_id=1,
        external_product_id="x",
        display_name="Bad",
        content_hash="h",
        data_quality_status="QUARANTINED",
        status="QUARANTINED",
    )
    offer = StoredOffer(
        id=1,
        product_id=1,
        merchant_id=1,
        current_price=1,
        currency="TRY",
        stock_status="AVAILABLE",
        content_hash="h",
        freshness_status="FRESH",
    )
    assert await product_to_search_candidate(product, offer) is None
