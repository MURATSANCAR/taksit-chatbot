"""ADR-010 — product search composition + alias cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from taksitlio.entity_resolution import EntityCandidate, ResolutionAction
from taksitlio.entity_resolution.cache import (
    InMemoryAliasResolutionCache,
    resolution_cache_key,
)
from taksitlio.product_query.ranking import RankingMode
from taksitlio.product_query.search import (
    ProductSearchRequest,
    SearchProductCandidate,
    search_products,
)


@pytest.mark.asyncio
async def test_search_resolves_merchant_and_ranks() -> None:
    merchants = (
        EntityCandidate(
            entity_id="m1",
            display_name="Example Merchant",
            canonical_name="Example Merchant",
            aliases=("exmple merchant",),
            entity_type="merchant",
        ),
    )
    products = (
        SearchProductCandidate(
            product_id="p-ok",
            display_name="Laptop",
            brand_model="X / 16",
            merchant_id="m1",
            merchant_display_name="Example Merchant",
            price=40000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            thumbnail_cdn_url="https://cdn.example.test/a.webp",
            finance_active=True,
            rate_fresh=True,
            best_monthly_payment=3500,
            best_total_repayment=42000,
            best_term_months=12,
            query_relevance=0.9,
            attribute_coverage=0.8,
            last_price_verified_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
        SearchProductCandidate(
            product_id="p-other",
            display_name="Phone",
            brand_model="Y",
            merchant_id="m2",
            merchant_display_name="Other",
            price=20000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            finance_active=True,
            rate_fresh=True,
            best_monthly_payment=2000,
            best_total_repayment=24000,
            last_price_verified_at=datetime.now(timezone.utc),
        ),
    )
    cache = InMemoryAliasResolutionCache()
    cache.set_version("v1")
    result = await search_products(
        ProductSearchRequest(
            utterance="laptop istiyorum",
            merchant_text="exmple merchant",
            max_price=45000,
            ranking_mode=RankingMode.LOWEST_MONTHLY_PAYMENT,
            phase="FIRST_CARDS",
            cache_version="v1",
        ),
        products=products,
        merchant_catalog=merchants,
        cache=cache,
    )
    assert result.merchant_resolution is not None
    assert result.merchant_resolution.action is ResolutionAction.AUTO_SELECT
    assert result.merchant_resolution.resolved_entity_id == "m1"
    ids = {c.product_id for c in result.result_phase.cards}
    assert "p-ok" in ids
    assert "p-other" not in ids  # filtered by merchant


@pytest.mark.asyncio
async def test_stale_enqueues_refresh_job() -> None:
    products = (
        SearchProductCandidate(
            product_id="p-stale",
            display_name="Laptop",
            brand_model=None,
            merchant_id="m1",
            merchant_display_name="M",
            price=10000,
            stock_status="AVAILABLE",
            price_freshness="STALE",
            has_primary_image=True,
            finance_active=True,
            rate_fresh=True,
            best_monthly_payment=900,
            best_total_repayment=10800,
            last_price_verified_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )
    result = await search_products(
        ProductSearchRequest(utterance="laptop", phase="FIRST_CARDS"),
        products=products,
    )
    assert result.refresh_jobs
    assert result.refresh_jobs[0].product_id == "p-stale"
    # Ranking safety requires FRESH for comparable offers (ADR-010 §208).
    assert all(c.product_id != "p-stale" for c in result.result_phase.cards)


@pytest.mark.asyncio
async def test_cache_version_change_clears_memory_cache() -> None:
    cache = InMemoryAliasResolutionCache()
    key = resolution_cache_key(
        entity_type="merchant", query_text="x", cache_version="v1"
    )
    await cache.put(key, {"ok": True}, ttl_seconds=60)
    assert await cache.get(key) is not None
    cache.set_version("v2")
    assert await cache.get(key) is None
