"""Field truth status + response outcome types (ADR-012 §3 / §18)."""

from __future__ import annotations

from enum import Enum


class FieldTruthStatus(str, Enum):
    VERIFIED = "VERIFIED"
    SOURCE_PROVIDED = "SOURCE_PROVIDED"
    CALCULATED = "CALCULATED"
    CALCULATED_ESTIMATE = "CALCULATED_ESTIMATE"
    INFERRED = "INFERRED"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    UNAVAILABLE = "UNAVAILABLE"


# Statuses that may appear as definitive claims in user-facing text.
CLAIMABLE_STATUSES: frozenset[FieldTruthStatus] = frozenset(
    {
        FieldTruthStatus.VERIFIED,
        FieldTruthStatus.SOURCE_PROVIDED,
        FieldTruthStatus.CALCULATED,
        FieldTruthStatus.CALCULATED_ESTIMATE,
    }
)

# Statuses that block "en uygun" / best-offer labeling.
UNSAFE_FOR_BEST_OFFER: frozenset[FieldTruthStatus] = frozenset(
    {
        FieldTruthStatus.CONFLICTED,
        FieldTruthStatus.STALE,
        FieldTruthStatus.UNAVAILABLE,
        FieldTruthStatus.INFERRED,
    }
)

UNSAFE_FOR_BEST_OFFER: frozenset[FieldTruthStatus] = frozenset(
    {
        FieldTruthStatus.INFERRED,
        FieldTruthStatus.STALE,
        FieldTruthStatus.CONFLICTED,
        FieldTruthStatus.UNAVAILABLE,
    }
)


class ResponseOutcome(str, Enum):
    ANSWERED = "ANSWERED"
    PARTIALLY_ANSWERED = "PARTIALLY_ANSWERED"
    CANNOT_VERIFY = "CANNOT_VERIFY"
    CLAIM_VALIDATION_FAILED = "CLAIM_VALIDATION_FAILED"
    PAYMENT_PLAN_RECONCILIATION_FAILED = "PAYMENT_PLAN_RECONCILIATION_FAILED"


class FinanceAvailability(str, Enum):
    """AVAILABLE ≠ RULE_ELIGIBLE ≠ PERSONAL_APPROVAL_REQUIRED (ADR-012 §8)."""

    AVAILABLE = "AVAILABLE"
    RULE_ELIGIBLE = "RULE_ELIGIBLE"
    PERSONAL_APPROVAL_REQUIRED = "PERSONAL_APPROVAL_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class CostKind(str, Enum):
    """ZERO_RATE ≠ ZERO_TOTAL_COST (ADR-012 §10)."""

    ZERO_RATE = "ZERO_RATE"
    ZERO_TOTAL_COST = "ZERO_TOTAL_COST"
    HAS_FEES = "HAS_FEES"
    INTEREST_BEARING = "INTEREST_BEARING"
    UNKNOWN = "UNKNOWN"


class ErrorClass(str, Enum):
    QUERY_UNDERSTANDING_ERROR = "QUERY_UNDERSTANDING_ERROR"
    ENTITY_RESOLUTION_ERROR = "ENTITY_RESOLUTION_ERROR"
    PRODUCT_IDENTITY_ERROR = "PRODUCT_IDENTITY_ERROR"
    STALE_PRICE_ERROR = "STALE_PRICE_ERROR"
    STOCK_ERROR = "STOCK_ERROR"
    BANK_MAPPING_ERROR = "BANK_MAPPING_ERROR"
    CAMPAIGN_MAPPING_ERROR = "CAMPAIGN_MAPPING_ERROR"
    PAYMENT_CALCULATION_ERROR = "PAYMENT_CALCULATION_ERROR"
    RANKING_ERROR = "RANKING_ERROR"
    LLM_EXPLANATION_ERROR = "LLM_EXPLANATION_ERROR"
    UI_DISPLAY_ERROR = "UI_DISPLAY_ERROR"
    SOURCE_DATA_ERROR = "SOURCE_DATA_ERROR"


def is_claimable(status: FieldTruthStatus) -> bool:
    return status in CLAIMABLE_STATUSES


__all__ = [
    "CLAIMABLE_STATUSES",
    "UNSAFE_FOR_BEST_OFFER",
    "CostKind",
    "ErrorClass",
    "FieldTruthStatus",
    "FinanceAvailability",
    "ResponseOutcome",
    "is_claimable",
]
