"""Campaign eligibility checks (ADR-010 §46 / §79)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from taksitlio.campaign_catalog.models import (
    CampaignStatus,
    FinanceCampaignRecord,
    VerificationStatus,
)


@dataclass(frozen=True)
class CampaignEligibilityInput:
    merchant_code: str
    purchase_amount: float
    term_months: int
    category_id: Optional[int] = None
    at: Optional[datetime] = None


@dataclass(frozen=True)
class CampaignEligibilityResult:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_campaign_eligibility(
    campaign: FinanceCampaignRecord,
    inp: CampaignEligibilityInput,
) -> CampaignEligibilityResult:
    reasons: list[str] = []
    now = inp.at or datetime.now(timezone.utc)

    if campaign.status is not CampaignStatus.ACTIVE:
        reasons.append("campaign_not_active")
    # Recovery-P1: UNVERIFIED campaigns must not be shown as active offers.
    if campaign.verification_status in {
        VerificationStatus.UNVERIFIED,
        VerificationStatus.CONFLICTED,
        VerificationStatus.EXPIRED,
        VerificationStatus.REJECTED,
    }:
        reasons.append(f"campaign_verification_{campaign.verification_status.value.lower()}")
    if not campaign.agreement_active:
        reasons.append("merchant_agreement_inactive")

    if campaign.valid_from and now < _aware(campaign.valid_from):
        reasons.append("not_yet_valid")
    if campaign.valid_until and now > _aware(campaign.valid_until):
        reasons.append("campaign_expired")

    if campaign.eligible_merchant_codes and inp.merchant_code not in campaign.eligible_merchant_codes:
        reasons.append("merchant_not_eligible")

    if (
        campaign.eligible_category_ids
        and inp.category_id is not None
        and inp.category_id not in campaign.eligible_category_ids
    ):
        reasons.append("category_not_eligible")

    if (
        campaign.minimum_purchase_amount is not None
        and inp.purchase_amount < campaign.minimum_purchase_amount
    ):
        reasons.append("below_minimum_amount")
    if (
        campaign.maximum_purchase_amount is not None
        and inp.purchase_amount > campaign.maximum_purchase_amount
    ):
        reasons.append("above_maximum_amount")

    if campaign.excluded_terms and inp.term_months in campaign.excluded_terms:
        reasons.append("term_excluded")
    if campaign.eligible_terms and inp.term_months not in campaign.eligible_terms:
        reasons.append("term_not_eligible")

    return CampaignEligibilityResult(eligible=not reasons, reasons=tuple(reasons))


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


__all__ = [
    "CampaignEligibilityInput",
    "CampaignEligibilityResult",
    "evaluate_campaign_eligibility",
]
