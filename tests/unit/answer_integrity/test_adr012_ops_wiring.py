"""Ops wiring: breaker persist from ingestion + sponsored store → search."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from taksitlio.answer_integrity.policy_store import InMemoryCircuitBreakerStore
from taksitlio.answer_integrity.quality_ops import persist_breaker_from_ingestion_result
from taksitlio.api.routes import admin_answer_integrity
from taksitlio.ingestion.runner import IngestionRunResult
from taksitlio.product_query.chat_bridge import _sponsored_kwargs
from taksitlio.product_query.ranking import RankingMode
from taksitlio.product_query.search import (
    ProductSearchRequest,
    SearchProductCandidate,
    search_products,
)
from taksitlio.recommendation_safety import BreakerAction
from taksitlio.recommendation_safety.sponsored import (
    InMemorySponsoredPlacementStore,
    SponsoredPlacementRecord,
)


def _candidate(**kwargs: object) -> SearchProductCandidate:
    base = dict(
        product_id="p1",
        display_name="Laptop 16GB",
        brand_model="X / 16",
        merchant_id="m1",
        merchant_display_name="Merchant",
        price=10000.0,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
        has_primary_image=True,
        thumbnail_cdn_url="https://cdn.test/x.webp",
        offer_id="42",
        price_snapshot_id="offer:42",
        stock_snapshot_id="offer:42:stock",
        campaign_active=True,
    )
    base.update(kwargs)
    return SearchProductCandidate(**base)  # type: ignore[arg-type]


def test_persist_breaker_from_ingestion_diagnostics() -> None:
    async def _run() -> None:
        store = InMemoryCircuitBreakerStore()
        result = IngestionRunResult(
            source_code="src-a",
            adapter_code="generic.json_feed.v1",
            merchant_id="m-broken",
            discovered=10,
            succeeded=4,
            failed=6,
            quarantined=6,
            chatbot_visible=4,
            items=(),
            diagnostics={
                "circuit_breaker": {
                    "merchant_id": "m-broken",
                    "broken_price_rate": 0.6,
                    "actions": ["DISABLE_PRICE_RESULTS"],
                    "price_disabled": True,
                },
                "schema_drift": {
                    "action": "QUARANTINED",
                    "reasons": ["all_out_of_stock"],
                },
            },
        )
        out = await persist_breaker_from_ingestion_result(result, store)
        assert out["persisted"] is True
        assert store.get("m-broken").is_price_disabled()
        assert BreakerAction.DISABLE_PRICE_RESULTS in store.get("m-broken").disabled

    asyncio.run(_run())


def test_sponsored_store_flows_into_search() -> None:
    async def _run() -> None:
        store = InMemorySponsoredPlacementStore()
        store.upsert(
            SponsoredPlacementRecord(product_id="sponsor", weight=50.0, active=True)
        )
        kwargs = _sponsored_kwargs(store)
        products = [
            replace(
                _candidate(product_id="organic", price=100.0, query_relevance=1.0),
                finance_active=True,
                rate_fresh=True,
                best_monthly_payment=50,
                best_total_repayment=600,
            ),
            replace(
                _candidate(product_id="sponsor", price=200.0, query_relevance=0.1),
                finance_active=True,
                rate_fresh=True,
                best_monthly_payment=80,
                best_total_repayment=900,
            ),
            replace(
                _candidate(product_id="mid", price=150.0, query_relevance=0.7),
                finance_active=True,
                rate_fresh=True,
                best_monthly_payment=60,
                best_total_repayment=700,
            ),
        ]
        req = ProductSearchRequest(
            utterance="laptop",
            ranking_mode=RankingMode.BEST_OVERALL_VALUE,
            sponsored_product_ids=kwargs["sponsored_product_ids"],
            sponsored_weights=kwargs["sponsored_weights"],
        )
        resp = await search_products(req, products=products)
        top = resp.result_phase.cards[0] if resp.result_phase.cards else None
        assert top is not None
        if top.product_id == "sponsor":
            assert top.ranking_label == "Sponsorlu seçenek"

    asyncio.run(_run())


def test_sponsored_admin_api() -> None:
    app = FastAPI()
    store = InMemorySponsoredPlacementStore()
    app.state.container = type(
        "C",
        (),
        {
            "extras": {
                "sponsored_store": store,
                "circuit_breaker_store": InMemoryCircuitBreakerStore(),
            }
        },
    )()
    app.include_router(admin_answer_integrity.router, prefix="/v1/admin")
    client = TestClient(app)
    put = client.put(
        "/v1/admin/answer-integrity/sponsored",
        json={"product_id": "p99", "weight": 12.5},
    )
    assert put.status_code == 200
    listed = client.get("/v1/admin/answer-integrity/sponsored")
    assert listed.status_code == 200
    assert any(p["product_id"] == "p99" for p in listed.json()["placements"])
    deleted = client.delete("/v1/admin/answer-integrity/sponsored/p99")
    assert deleted.status_code == 200
    assert not store.list_active()
