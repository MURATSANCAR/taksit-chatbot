"""ADR-011 hydrate + ADR-012 feedback/shadow/sponsored wiring tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container
from taksitlio.product_query.ranking import RankableProduct, RankingMode, rank_products
from taksitlio.query_state import QueryNeedState
from taksitlio.recommendation_safety.feedback import SponsoredPlacement
from taksitlio.search_sessions import build_demo_orchestrator
from taksitlio.search_sessions.hydrate import SessionSnapshot, hydrate_orchestrator
from taksitlio.search_sessions.repository import QueryVersion, SearchSession, SessionEvent
from taksitlio.search_sessions.status import SearchSessionStatus


def test_hydrate_orchestrator_restores_runtime_state() -> None:
    orch = build_demo_orchestrator()
    sid = "11111111-1111-1111-1111-111111111111"
    session = SearchSession(
        id=sid,
        conversation_id="22222222-2222-2222-2222-222222222222",
        status=SearchSessionStatus.WAITING_USER_ANSWER,
        active_query_version=1,
        clarification_count=1,
        metadata={
            "need_state": QueryNeedState(budget={"maximum": 40000}, state_version=2).to_dict(),
            "logos": {
                "merchant": [
                    {
                        "entity_id": "merchant-teknosa",
                        "display_name": "Teknosa",
                        "logo_cdn_url": "https://cdn.test/m.webp",
                        "kind": "merchant",
                    }
                ]
            },
        },
    )
    snap = SessionSnapshot(
        session=session,
        versions=[
            QueryVersion(
                id="33333333-3333-3333-3333-333333333333",
                search_session_id=sid,
                version_number=1,
                raw_user_text="laptop",
                normalized_text="laptop",
                state_snapshot={"intent": "product_search"},
            )
        ],
        events=[
            SessionEvent(
                id="44444444-4444-4444-4444-444444444444",
                search_session_id=sid,
                query_version=1,
                event_type="CLARIFICATION_ASKED",
                display_message="Hangi ürün türünü arıyorsunuz?",
            )
        ],
        clarifications=[
            {
                "clarification_id": "c1",
                "query_version": 1,
                "field": "category",
                "question_text": "Ne arıyorsunuz?",
                "question_signature": "cat",
                "options": [{"id": "laptop", "label": "Laptop"}],
                "status": "PENDING",
            }
        ],
        metadata=session.metadata,
    )
    loaded = hydrate_orchestrator(orch, snap)
    assert loaded.id == sid
    assert orch.repo.get(sid) is not None
    assert orch.states[sid].budget.get("maximum") == 40000
    assert orch.logo_rails[sid]["merchant"][0].logo_cdn_url == "https://cdn.test/m.webp"
    assert orch.clarifications[sid]["clarification_id"] == "c1"
    assert orch.parses[sid]["intent"] == "product_search"


def test_sponsored_isolation_never_steals_best_slot() -> None:
    items = [
        RankableProduct(
            product_id="organic-best",
            price=10000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            query_relevance=1.0,
            best_monthly_payment=800,
            best_total_repayment=9600,
            finance_active=True,
            rate_fresh=True,
        ),
        RankableProduct(
            product_id="sponsored-mid",
            price=12000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            query_relevance=0.5,
            best_monthly_payment=900,
            best_total_repayment=10800,
            finance_active=True,
            rate_fresh=True,
        ),
        RankableProduct(
            product_id="organic-other",
            price=11000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            query_relevance=0.7,
            best_monthly_payment=850,
            best_total_repayment=10200,
            finance_active=True,
            rate_fresh=True,
        ),
    ]
    ranked = rank_products(
        items,
        mode=RankingMode.BEST_OVERALL_VALUE,
        min_comparison_count_for_best_label=3,
        sponsored=[SponsoredPlacement("sponsored-mid", weight=99.0)],
    )
    assert ranked[0].product_id == "organic-best"
    assert ranked[0].label == "En uygun"
    assert "sponsored-mid" in {r.product_id for r in ranked}


@pytest.mark.asyncio
async def test_feedback_and_shadow_api() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fb = await client.post(
            "/v1/feedback",
            json={
                "query_version": 1,
                "parsed_constraints": {"budget": 40000},
                "selected_product": "p1",
                "error_class": "RANKING_ERROR",
            },
        )
        assert fb.status_code == 200
        bad = await client.post(
            "/v1/feedback",
            json={"query_version": 1, "error_class": "WRONG_ANSWER"},
        )
        assert bad.status_code == 422
        sh = await client.post(
            "/v1/shadow-comparisons",
            json={
                "comparison_key": "k1",
                "live_payload": {"product_ids": ["a"]},
                "shadow_payload": {"product_ids": ["b"]},
            },
        )
        assert sh.status_code == 200
        assert "product_ids" in sh.json()["diffs"]
        metrics = await client.get("/v1/error-class-events/summary")
        assert metrics.status_code == 200
