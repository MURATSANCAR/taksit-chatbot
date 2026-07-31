"""ADR-010 P3 — campaign eligibility tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from taksitlio.campaign_catalog import (
    CampaignEligibilityInput,
    CampaignStatus,
    CampaignType,
    FinanceCampaignRecord,
    evaluate_campaign_eligibility,
)


def _campaign(**kwargs) -> FinanceCampaignRecord:
    base = dict(
        campaign_code="c1",
        institution_code="bank-x",
        display_name="Example Campaign",
        campaign_type=CampaignType.INSTALLMENT,
        status=CampaignStatus.ACTIVE,
        agreement_active=True,
        eligible_merchant_codes=("m1",),
        eligible_terms=(6, 9, 12),
    )
    base.update(kwargs)
    return FinanceCampaignRecord(**base)


def test_eligible_happy_path() -> None:
    result = evaluate_campaign_eligibility(
        _campaign(),
        CampaignEligibilityInput(merchant_code="m1", purchase_amount=10000, term_months=12),
    )
    assert result.eligible is True


def test_expired_campaign_rejected() -> None:
    past = datetime.now(timezone.utc) - timedelta(days=1)
    result = evaluate_campaign_eligibility(
        _campaign(valid_until=past),
        CampaignEligibilityInput(merchant_code="m1", purchase_amount=10000, term_months=12),
    )
    assert result.eligible is False
    assert "campaign_expired" in result.reasons


def test_missing_agreement_rejected() -> None:
    result = evaluate_campaign_eligibility(
        _campaign(agreement_active=False),
        CampaignEligibilityInput(merchant_code="m1", purchase_amount=10000, term_months=12),
    )
    assert result.eligible is False
    assert "merchant_agreement_inactive" in result.reasons


def test_term_and_amount_bounds() -> None:
    camp = _campaign(minimum_purchase_amount=5000, maximum_purchase_amount=20000, excluded_terms=(9,))
    low = evaluate_campaign_eligibility(
        camp,
        CampaignEligibilityInput(merchant_code="m1", purchase_amount=1000, term_months=12),
    )
    assert "below_minimum_amount" in low.reasons
    excl = evaluate_campaign_eligibility(
        camp,
        CampaignEligibilityInput(merchant_code="m1", purchase_amount=10000, term_months=9),
    )
    assert "term_excluded" in excl.reasons
