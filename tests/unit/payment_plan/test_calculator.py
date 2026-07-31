"""ADR-010 P3 — payment plan calculator tests."""

from __future__ import annotations

import pytest

from taksitlio.campaign_catalog.models import RateSnapshotRecord, RateType
from taksitlio.ingestion.errors import RateUnavailable
from taksitlio.payment_plan import (
    FORBIDDEN_CERTAIN_LABEL,
    LABEL_ESTIMATE,
    LABEL_SOURCE,
    PaymentPlanKind,
    SourceProvidedOffer,
    assert_safe_display_label,
    calculate_annuity_payment,
    calculate_estimate_from_rate,
    from_source_provided_offer,
)


def test_zero_rate_estimate() -> None:
    snap = RateSnapshotRecord(
        financial_product_code="fp1",
        rate_type=RateType.ZERO_RATE,
        freshness_status="FRESH",
        source_reference="rate://zero",
    )
    plan = calculate_estimate_from_rate(
        purchase_price=12000,
        term_months=12,
        snapshot=snap,
    )
    assert plan.plan_kind is PaymentPlanKind.CALCULATED_ESTIMATE
    assert plan.display_label == LABEL_ESTIMATE
    assert plan.monthly_payment == 1000.0
    assert plan.total_repayment == 12000.0
    assert plan.monthly_rate == 0.0
    assert len(plan.installments) == 12


def test_interest_annuity_estimate() -> None:
    snap = RateSnapshotRecord(
        financial_product_code="fp1",
        rate_type=RateType.INTEREST,
        monthly_rate=0.02,
        freshness_status="FRESH",
        source_reference="rate://2pct",
    )
    plan = calculate_estimate_from_rate(
        purchase_price=10000,
        term_months=12,
        snapshot=snap,
    )
    expected = calculate_annuity_payment(10000, 0.02, 12)
    assert plan.monthly_payment == expected
    assert plan.total_repayment > 10000


def test_missing_rate_not_invented() -> None:
    snap = RateSnapshotRecord(
        financial_product_code="fp1",
        rate_type=RateType.INTEREST,
        monthly_rate=None,
        freshness_status="FRESH",
    )
    with pytest.raises(RateUnavailable):
        calculate_estimate_from_rate(purchase_price=10000, term_months=12, snapshot=snap)


def test_expired_rate_rejected() -> None:
    snap = RateSnapshotRecord(
        financial_product_code="fp1",
        rate_type=RateType.ZERO_RATE,
        freshness_status="EXPIRED",
    )
    with pytest.raises(RateUnavailable):
        calculate_estimate_from_rate(purchase_price=10000, term_months=12, snapshot=snap)


def test_source_provided_offer_label() -> None:
    plan = from_source_provided_offer(
        purchase_price=12000,
        term_months=12,
        offer=SourceProvidedOffer(monthly_payment=1100, total_repayment=13200, source_reference="src://x"),
    )
    assert plan.plan_kind is PaymentPlanKind.SOURCE_PROVIDED_OFFER
    assert plan.display_label == LABEL_SOURCE
    assert plan.monthly_payment == 1100


def test_forbidden_certain_label() -> None:
    with pytest.raises(ValueError):
        assert_safe_display_label(FORBIDDEN_CERTAIN_LABEL)
