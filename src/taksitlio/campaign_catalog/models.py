"""Campaign / rate domain records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Optional


class CampaignType(str, Enum):
    RATE_DISCOUNT = "RATE_DISCOUNT"
    ZERO_RATE = "ZERO_RATE"
    DEFERRED_PAYMENT = "DEFERRED_PAYMENT"
    INSTALLMENT = "INSTALLMENT"
    FEE_DISCOUNT = "FEE_DISCOUNT"
    MERCHANT_SPECIAL = "MERCHANT_SPECIAL"
    CATEGORY_SPECIAL = "CATEGORY_SPECIAL"
    PRODUCT_SPECIAL = "PRODUCT_SPECIAL"


class CampaignStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    EXPIRED = "EXPIRED"


class VerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class RateType(str, Enum):
    INTEREST = "INTEREST"
    PROFIT_RATE = "PROFIT_RATE"
    FIXED_PAYMENT = "FIXED_PAYMENT"
    ADVERTISED_PAYMENT = "ADVERTISED_PAYMENT"
    ZERO_RATE = "ZERO_RATE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FinanceCampaignRecord:
    campaign_code: str
    institution_code: str
    display_name: str
    campaign_type: CampaignType
    status: CampaignStatus = CampaignStatus.DRAFT
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    minimum_purchase_amount: Optional[float] = None
    maximum_purchase_amount: Optional[float] = None
    eligible_terms: tuple[int, ...] = ()
    excluded_terms: tuple[int, ...] = ()
    eligible_merchant_codes: tuple[str, ...] = ()
    eligible_category_ids: tuple[int, ...] = ()
    agreement_active: bool = False
    source_reference: Optional[str] = None


@dataclass(frozen=True)
class RateSnapshotRecord:
    financial_product_code: str
    rate_type: RateType
    monthly_rate: Optional[float] = None
    annual_cost_rate: Optional[float] = None
    profit_rate: Optional[float] = None
    minimum_amount: Optional[float] = None
    maximum_amount: Optional[float] = None
    minimum_term: Optional[int] = None
    maximum_term: Optional[int] = None
    term_rates: Mapping[int, float] = field(default_factory=dict)
    freshness_status: str = "UNVERIFIED"
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    source_reference: Optional[str] = None
    campaign_code: Optional[str] = None


__all__ = [
    "CampaignStatus",
    "CampaignType",
    "FinanceCampaignRecord",
    "RateSnapshotRecord",
    "RateType",
    "VerificationStatus",
]
