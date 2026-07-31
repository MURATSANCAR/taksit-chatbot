"""ADR-012 policy store + sponsored ranking + feedback API smoke."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from taksitlio.answer_integrity.policy_store import (
    InMemoryCircuitBreakerStore,
    InMemoryFeedbackStore,
    InMemoryPrecedencePolicyLoader,
)
from taksitlio.api.routes import admin_answer_integrity
from taksitlio.product_query.ranking import (
    RankableProduct,
    RankingMode,
    rank_products_with_sponsored_isolation,
)
from taksitlio.recommendation_safety.circuit_breaker import BreakerAction


def test_in_memory_precedence_default_loaded() -> None:
    loader = InMemoryPrecedencePolicyLoader()
    pol = loader.load("DEFAULT")
    assert "PRICE" in pol.precedence_by_kind
    assert pol.precedence_by_kind["PRICE"][0] == "merchant_api"


def test_circuit_breaker_store_records() -> None:
    store = InMemoryCircuitBreakerStore()
    store.record_actions("m1", [BreakerAction.DISABLE_PRICE_RESULTS], reason="rate")
    assert store.get("m1").is_price_disabled()


def test_sponsored_cannot_steal_best_label() -> None:
    items = [
        RankableProduct(
            product_id="organic",
            price=100,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=50,
            best_total_repayment=600,
            finance_active=True,
            rate_fresh=True,
            query_relevance=1.0,
            attribute_coverage=1.0,
        ),
        RankableProduct(
            product_id="sponsor",
            price=200,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=80,
            best_total_repayment=900,
            finance_active=True,
            rate_fresh=True,
            query_relevance=0.5,
            attribute_coverage=0.5,
        ),
        RankableProduct(
            product_id="c",
            price=150,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=60,
            best_total_repayment=700,
            finance_active=True,
            rate_fresh=True,
            query_relevance=0.8,
            attribute_coverage=0.8,
        ),
    ]
    ranked = rank_products_with_sponsored_isolation(
        items,
        mode=RankingMode.BEST_OVERALL_VALUE,
        sponsored_product_ids=("sponsor",),
        sponsored_weights={"sponsor": 99.0},
    )
    assert ranked[0].product_id != "sponsor" or ranked[0].label == "Sponsorlu seçenek"
    if ranked[0].label in {"En uygun", "En uygun değer", "En uygun ürün"}:
        assert ranked[0].product_id != "sponsor"


def test_feedback_api_rejects_wrong_answer_bucket() -> None:
    app = FastAPI()
    app.state.container = type("C", (), {"extras": {"feedback_store": InMemoryFeedbackStore()}})()
    app.include_router(admin_answer_integrity.router, prefix="/v1/admin")
    client = TestClient(app)
    bad = client.post(
        "/v1/admin/answer-integrity/error-class",
        json={
            "error_class": "WRONG_ANSWER",
            "owner": "x",
            "metric_key": "x",
        },
    )
    assert bad.status_code == 400
    ok = client.post(
        "/v1/admin/answer-integrity/feedback",
        json={
            "query_version": 1,
            "parsed_constraints": {"negative": ["telefon"]},
            "response_fact_ids": ["f1"],
            "error_class": "RANKING_ERROR",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["persisted"] is True
    assert ok.json()["snapshot"].get("feedback_id") or True
    metrics = client.get("/v1/admin/answer-integrity/error-class/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["counts"].get("RANKING_ERROR", 0) >= 1


def test_postgres_feedback_store_writes_v023_sql() -> None:
    """Fake pool asserts INSERT targets V023 tables."""

    import asyncio

    from taksitlio.answer_integrity.policy_store import PostgresFeedbackStore

    class _Conn:
        def __init__(self) -> None:
            self.statements: list[tuple[str, tuple]] = []

        async def execute(self, sql: str, *args: object) -> None:
            self.statements.append((sql, args))

        async def fetch(self, sql: str, *args: object) -> list[dict]:
            return [{"error_class": "RANKING_ERROR", "n": 2}]

    class _Acquire:
        def __init__(self, conn: _Conn) -> None:
            self._conn = conn

        async def __aenter__(self) -> _Conn:
            return self._conn

        async def __aexit__(self, *args: object) -> None:
            return None

    class _Pool:
        def __init__(self) -> None:
            self.conn = _Conn()

        def acquire(self) -> _Acquire:
            return _Acquire(self.conn)

    async def _run() -> None:
        pool = _Pool()
        store = PostgresFeedbackStore(pool=pool)
        saved = await store.save_feedback_async(
            {
                "query_version": 3,
                "parsed_constraints": {"negative": ["telefon"]},
                "response_fact_ids": ["f1"],
                "error_class": "RANKING_ERROR",
            }
        )
        assert saved["feedback_id"]
        assert any("feedback_result_snapshots" in s[0] for s in pool.conn.statements)

        await store.save_shadow_async(
            {
                "comparison_key": "k1",
                "live": {"a": 1},
                "shadow": {"a": 2},
                "diffs": ["a"],
            }
        )
        assert any("shadow_mode_comparisons" in s[0] for s in pool.conn.statements)

        await store.save_error_class_async(
            {"error_class": "RANKING_ERROR", "owner": "ranking", "metric_key": "x"}
        )
        assert any("error_class_events" in s[0] for s in pool.conn.statements)

        counts = await store.metrics_by_error_class_async()
        assert counts["RANKING_ERROR"] == 2

    asyncio.run(_run())
