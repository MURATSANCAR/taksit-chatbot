"""Finance campaign catalog domain (ADR-010 §45–46).

Personalized credit approval stays behind ADR-009 Campaign Gate.
"""

from __future__ import annotations

from taksitlio.campaign_catalog.eligibility import CampaignEligibilityInput, evaluate_campaign_eligibility
from taksitlio.campaign_catalog.models import (
    CampaignStatus,
    CampaignType,
    FinanceCampaignRecord,
    RateSnapshotRecord,
    RateType,
    VerificationStatus,
)

ADR_SCOPE = "ADR-010"
PACKAGE_STATUS = "P3"

__all__ = [
    "ADR_SCOPE",
    "PACKAGE_STATUS",
    "CampaignEligibilityInput",
    "CampaignStatus",
    "CampaignType",
    "FinanceCampaignRecord",
    "RateSnapshotRecord",
    "RateType",
    "VerificationStatus",
    "evaluate_campaign_eligibility",
]
