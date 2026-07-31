"""ADR-010 P5 — freshness scheduler + merchant activation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from taksitlio.ingestion_scheduler import (
    PRIORITY_USER_SEARCH_STALE,
    SchedulerQueue,
    classify_freshness,
    enqueue_search_driven_refresh,
)
from taksitlio.merchant import (
    MerchantActivationGate,
    MerchantReadinessSignals,
    evaluate_merchant_activation,
)


def test_fresh_no_enqueue() -> None:
    now = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    verdict = classify_freshness(
        last_verified_at=now - timedelta(minutes=10),
        ttl_seconds=3600,
        now=now,
    )
    assert verdict.status == "FRESH"
    assert verdict.show_as_current_offer is True
    assert enqueue_search_driven_refresh(product_id="p1", verdict=verdict) is None


def test_stale_search_driven_high_priority() -> None:
    now = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    verdict = classify_freshness(
        last_verified_at=now - timedelta(hours=2),
        ttl_seconds=3600,
        now=now,
        user_search_driven=True,
        queue_on_stale=SchedulerQueue.PRICE_REFRESH,
    )
    assert verdict.status == "STALE"
    assert verdict.show_as_current_offer is True
    job = enqueue_search_driven_refresh(product_id="p1", verdict=verdict)
    assert job is not None
    assert job.priority == PRIORITY_USER_SEARCH_STALE
    assert job.queue_name is SchedulerQueue.PRICE_REFRESH


def test_expired_hidden_from_current_offers() -> None:
    now = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    verdict = classify_freshness(
        last_verified_at=now - timedelta(hours=10),
        ttl_seconds=3600,
        now=now,
    )
    assert verdict.status == "EXPIRED"
    assert verdict.show_as_current_offer is False


def test_merchant_blocked_until_verified() -> None:
    decision = evaluate_merchant_activation(MerchantReadinessSignals())
    assert decision.gate is MerchantActivationGate.BLOCKED
    assert decision.allowed_data_kinds == ()


def test_merchant_partial_and_ready() -> None:
    partial = evaluate_merchant_activation(
        MerchantReadinessSignals(
            merchant_verified=True,
            source_healthy=True,
            price_freshness_ok=True,
        )
    )
    assert partial.gate is MerchantActivationGate.PARTIAL
    assert "prices" in partial.allowed_data_kinds
    assert "campaigns" not in partial.allowed_data_kinds

    ready = evaluate_merchant_activation(
        MerchantReadinessSignals(
            merchant_verified=True,
            source_healthy=True,
            product_coverage_ok=True,
            image_coverage_ok=True,
            price_freshness_ok=True,
            bank_agreements_verified=True,
            campaign_mapping_verified=True,
            payment_calculations_tested=True,
        )
    )
    assert ready.gate is MerchantActivationGate.READY
