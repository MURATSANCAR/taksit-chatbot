"""ADR-011 P2 — logos, latency metrics, catalog pool, finance logo enrichment."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container
from taksitlio.chatbot_cards import ProductCardFinanceSummary
from taksitlio.media.logo_resolver import InMemoryLogoCatalog, LogoResolver
from taksitlio.product_query.finance_index import (
    InstitutionLabelResolver,
    enrich_candidate_with_finance,
)
from taksitlio.product_query.finance_projection import ProductFinanceOptionRow
from taksitlio.product_query.search import SearchProductCandidate
from taksitlio.search_sessions import build_demo_orchestrator
from taksitlio.search_sessions.metrics import GLOBAL_SEARCH_METRICS, percentile


def test_percentile_helpers() -> None:
    assert percentile([], 50) is None
    assert percentile([10.0], 95) == 10.0
    assert percentile([1, 2, 3, 4, 5], 50) == 3.0


def test_logo_resolver_and_orchestrator_rail() -> None:
    orch = build_demo_orchestrator()
    catalog = InMemoryLogoCatalog()
    catalog.put_merchant("merchant-teknosa", "https://cdn.test/m.webp")
    catalog.put_institution("institution-kuveyt", "https://cdn.test/b.webp")
    orch.logo_resolver = catalog.resolver
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-000000000021",
        message="Teknoksa’dan 40 bin liraya laptop, 12 ay Kuveyt Türk",
    )
    logos = out["logos"]
    assert logos["merchant"][0]["logo_cdn_url"] == "https://cdn.test/m.webp"
    assert logos["institution"][0]["logo_cdn_url"] == "https://cdn.test/b.webp"
    assert any(
        m["metric_name"] == "partial_result_latency_ms" or m["metric_name"] == "search_complete_ms"
        for m in orch.repo.metrics.get(out["search_session_id"], [])
    ) or out["status"] == "COMPLETED"


def test_institution_finance_logo_on_card() -> None:
    cand = SearchProductCandidate(
        product_id="1",
        display_name="Phone",
        brand_model=None,
        merchant_id="1",
        merchant_display_name="M",
        price=10000,
    )
    row = ProductFinanceOptionRow(
        product_offer_id="1",
        merchant_id="1",
        institution_id="7",
        term_months=12,
        monthly_payment=900,
        total_repayment=10800,
        fees_total=0,
        eligibility_status="ELIGIBLE",
        plan_kind="CALCULATED_ESTIMATE",
        freshness_status="FRESH",
        campaign_id=None,
        rate_snapshot_id=None,
        display_label="Tahmini aylık ödeme",
    )
    resolver = InstitutionLabelResolver(
        labels={"7": "Example Bank"},
        logos={"7": "https://cdn.test/bank.webp"},
    )
    enriched = enrich_candidate_with_finance(cand, [row], institutions=resolver)
    assert enriched.card_finance is not None
    assert isinstance(enriched.card_finance, ProductCardFinanceSummary)
    assert enriched.card_finance.institution_logo_cdn_url == "https://cdn.test/bank.webp"


@pytest.mark.asyncio
async def test_metrics_summary_endpoint() -> None:
    GLOBAL_SEARCH_METRICS.observations.clear()
    GLOBAL_SEARCH_METRICS.counters.clear()
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/search-sessions",
            json={
                "conversation_id": "00000000-0000-0000-0000-000000000022",
                "message": "40 bin liraya televizyon",
            },
        )
        resp = await client.get("/v1/search-sessions/metrics/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "latencies" in body
    assert "search_complete_ms" in body["latencies"]
    await container.aclose()


@pytest.mark.asyncio
async def test_in_memory_container_exposes_logo_resolver() -> None:
    container = build_in_memory_container()
    resolver = container.extras.get("logo_resolver")
    assert isinstance(resolver, LogoResolver)
    assert resolver.merchant("merchant-teknosa")
    await container.aclose()
