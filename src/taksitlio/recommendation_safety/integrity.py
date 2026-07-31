"""Recommendation integrity + reason codes (ADR-012 §13–14)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


class RecommendationReasonCode(str, Enum):
    REQUIRED_ATTRIBUTES_MATCHED = "REQUIRED_ATTRIBUTES_MATCHED"
    WITHIN_BUDGET = "WITHIN_BUDGET"
    LOWEST_TOTAL_REPAYMENT = "LOWEST_TOTAL_REPAYMENT"
    LOWEST_MONTHLY_PAYMENT = "LOWEST_MONTHLY_PAYMENT"
    LOWEST_PRODUCT_PRICE = "LOWEST_PRODUCT_PRICE"
    STOCK_VERIFIED = "STOCK_VERIFIED"
    FRESH_PRICE = "FRESH_PRICE"
    FINANCE_MAPPING_VERIFIED = "FINANCE_MAPPING_VERIFIED"
    CAMPAIGN_ACTIVE = "CAMPAIGN_ACTIVE"


BEST_LABEL = "En uygun ürün"
NEAREST_LABEL = "Kriterlerinize en yakın seçenek"

REQUIRED_BEST_CONDITIONS: tuple[str, ...] = (
    "min_comparable_candidates",
    "prices_fresh",
    "stock_verified",
    "variants_comparable",
    "total_repayment_present",
    "finance_mapping_verified",
    "campaign_active",
    "critical_attributes_complete",
)


@dataclass(frozen=True)
class RecommendationCandidate:
    product_id: str
    price_fresh: bool
    stock_verified: bool
    variants_comparable: bool
    total_repayment: Optional[float]
    monthly_payment: Optional[float]
    price: Optional[float]
    finance_mapping_verified: bool
    campaign_active: bool
    critical_attributes_complete: bool
    within_budget: bool = True
    required_attributes_matched: bool = True
    conflicted: bool = False


@dataclass(frozen=True)
class RecommendationIntegrityResult:
    best_label_allowed: bool
    label: str
    missing_conditions: tuple[str, ...]
    reason_codes: tuple[str, ...]
    winners: dict[str, Optional[str]]  # price / monthly / total → product_id


def evaluate_recommendation_integrity(
    candidates: Sequence[RecommendationCandidate],
    *,
    min_comparable: int = 3,
    winner_product_id: Optional[str] = None,
) -> RecommendationIntegrityResult:
    comparable = [
        c
        for c in candidates
        if not c.conflicted
        and c.price_fresh
        and c.stock_verified
        and c.variants_comparable
        and c.total_repayment is not None
        and c.finance_mapping_verified
        and c.campaign_active
        and c.critical_attributes_complete
    ]
    missing: list[str] = []
    if len(comparable) < min_comparable:
        missing.append("min_comparable_candidates")

    # Check aggregate conditions across candidate set intended for "best"
    if not candidates:
        missing.extend(
            [
                "prices_fresh",
                "stock_verified",
                "variants_comparable",
                "total_repayment_present",
                "finance_mapping_verified",
                "campaign_active",
                "critical_attributes_complete",
            ]
        )
    else:
        if not all(c.price_fresh for c in candidates if not c.conflicted):
            missing.append("prices_fresh")
        if not all(c.stock_verified for c in candidates if not c.conflicted):
            missing.append("stock_verified")
        if not all(c.variants_comparable for c in candidates if not c.conflicted):
            missing.append("variants_comparable")
        if not all(
            c.total_repayment is not None for c in candidates if not c.conflicted
        ):
            missing.append("total_repayment_present")
        if not all(
            c.finance_mapping_verified for c in candidates if not c.conflicted
        ):
            missing.append("finance_mapping_verified")
        if not all(c.campaign_active for c in candidates if not c.conflicted):
            missing.append("campaign_active")
        if not all(
            c.critical_attributes_complete for c in candidates if not c.conflicted
        ):
            missing.append("critical_attributes_complete")

    # Conflicted never best
    if winner_product_id:
        for c in candidates:
            if c.product_id == winner_product_id and c.conflicted:
                missing.append("winner_conflicted")

    unique_missing = tuple(dict.fromkeys(missing))
    best_ok = len(unique_missing) == 0 and len(comparable) >= min_comparable
    label = BEST_LABEL if best_ok else NEAREST_LABEL

    reason_codes: list[str] = []
    focus = None
    if winner_product_id:
        focus = next((c for c in candidates if c.product_id == winner_product_id), None)
    if focus is None and comparable:
        focus = comparable[0]
    if focus is not None:
        if focus.required_attributes_matched:
            reason_codes.append(RecommendationReasonCode.REQUIRED_ATTRIBUTES_MATCHED.value)
        if focus.within_budget:
            reason_codes.append(RecommendationReasonCode.WITHIN_BUDGET.value)
        if focus.stock_verified:
            reason_codes.append(RecommendationReasonCode.STOCK_VERIFIED.value)
        if focus.price_fresh:
            reason_codes.append(RecommendationReasonCode.FRESH_PRICE.value)
        if focus.finance_mapping_verified:
            reason_codes.append(RecommendationReasonCode.FINANCE_MAPPING_VERIFIED.value)
        if focus.campaign_active:
            reason_codes.append(RecommendationReasonCode.CAMPAIGN_ACTIVE.value)

    winners = three_winners(candidates)
    # Annotate lowest-* reason codes for winner
    if focus is not None:
        if winners.get("price") == focus.product_id:
            reason_codes.append(RecommendationReasonCode.LOWEST_PRODUCT_PRICE.value)
        if winners.get("monthly") == focus.product_id:
            reason_codes.append(RecommendationReasonCode.LOWEST_MONTHLY_PAYMENT.value)
        if winners.get("total") == focus.product_id:
            reason_codes.append(RecommendationReasonCode.LOWEST_TOTAL_REPAYMENT.value)

    return RecommendationIntegrityResult(
        best_label_allowed=best_ok,
        label=label,
        missing_conditions=unique_missing,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        winners=winners,
    )


def three_winners(
    candidates: Sequence[RecommendationCandidate],
) -> dict[str, Optional[str]]:
    """Separate winners: lowest price / monthly / total repayment."""

    def _min_id(attr: str) -> Optional[str]:
        best_id = None
        best_val = None
        for c in candidates:
            if c.conflicted:
                continue
            val = getattr(c, attr)
            if val is None:
                continue
            if best_val is None or val < best_val:
                best_val = val
                best_id = c.product_id
        return best_id

    return {
        "price": _min_id("price"),
        "monthly": _min_id("monthly_payment"),
        "total": _min_id("total_repayment"),
    }


__all__ = [
    "BEST_LABEL",
    "NEAREST_LABEL",
    "REQUIRED_BEST_CONDITIONS",
    "RecommendationCandidate",
    "RecommendationIntegrityResult",
    "RecommendationReasonCode",
    "evaluate_recommendation_integrity",
    "three_winners",
]
