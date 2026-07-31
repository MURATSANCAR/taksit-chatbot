"""Shadow mode comparison + feedback snapshots + sponsored isolation (ADR-012 §21–25)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from taksitlio.answer_integrity.truth_status import ErrorClass


@dataclass(frozen=True)
class ShadowComparison:
    comparison_id: str
    query_version: str
    baseline_text: str
    candidate_text: str
    baseline_fact_ids: tuple[str, ...]
    candidate_fact_ids: tuple[str, ...]
    diverged: bool
    divergence_reasons: tuple[str, ...]
    shown_to_user: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def compare_shadow(
    *,
    comparison_id: str,
    query_version: str,
    baseline_text: str,
    candidate_text: str,
    baseline_fact_ids: Sequence[str],
    candidate_fact_ids: Sequence[str],
) -> ShadowComparison:
    reasons: list[str] = []
    if baseline_text.strip() != candidate_text.strip():
        reasons.append("text_diverged")
    if tuple(baseline_fact_ids) != tuple(candidate_fact_ids):
        reasons.append("fact_ids_diverged")
    return ShadowComparison(
        comparison_id=comparison_id,
        query_version=query_version,
        baseline_text=baseline_text,
        candidate_text=candidate_text,
        baseline_fact_ids=tuple(baseline_fact_ids),
        candidate_fact_ids=tuple(candidate_fact_ids),
        diverged=bool(reasons),
        divergence_reasons=tuple(reasons),
        shown_to_user=False,
    )


@dataclass(frozen=True)
class FeedbackResultSnapshot:
    feedback_id: str
    query_version: str
    parsed_constraints: Mapping[str, Any]
    catalog_revision: Optional[str]
    price_snapshot_id: Optional[str]
    campaign_snapshot_id: Optional[str]
    selected_product_id: Optional[str]
    selected_bank: Optional[str]
    response_fact_ids: tuple[str, ...]
    user_note: str = ""
    error_class: Optional[ErrorClass] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ErrorClassEvent:
    event_id: str
    error_class: ErrorClass
    owner: str
    metric_key: str
    detail: str = ""
    source_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


ERROR_CLASS_OWNERS: Mapping[ErrorClass, tuple[str, str]] = {
    ErrorClass.QUERY_UNDERSTANDING_ERROR: ("understanding", "understanding_error_rate"),
    ErrorClass.ENTITY_RESOLUTION_ERROR: ("entity_resolution", "entity_error_rate"),
    ErrorClass.PRODUCT_IDENTITY_ERROR: ("catalog", "identity_error_rate"),
    ErrorClass.STALE_PRICE_ERROR: ("ingestion", "stale_price_shown_rate"),
    ErrorClass.STOCK_ERROR: ("ingestion", "stock_error_rate"),
    ErrorClass.BANK_MAPPING_ERROR: ("finance", "bank_mapping_error_rate"),
    ErrorClass.CAMPAIGN_MAPPING_ERROR: ("finance", "campaign_mapping_error_rate"),
    ErrorClass.PAYMENT_CALCULATION_ERROR: ("payment_plan", "payment_calc_error_rate"),
    ErrorClass.RANKING_ERROR: ("ranking", "ranking_error_rate"),
    ErrorClass.LLM_EXPLANATION_ERROR: ("answer_integrity", "llm_claim_fail_rate"),
    ErrorClass.UI_DISPLAY_ERROR: ("ui", "ui_display_error_rate"),
    ErrorClass.SOURCE_DATA_ERROR: ("ingestion", "source_data_error_rate"),
}


def classify_feedback_error(error_class: ErrorClass) -> ErrorClassEvent:
    owner, metric = ERROR_CLASS_OWNERS[error_class]
    return ErrorClassEvent(
        event_id=f"evt-{error_class.value}",
        error_class=error_class,
        owner=owner,
        metric_key=metric,
    )


@dataclass(frozen=True)
class RankedSlot:
    product_id: str
    organic_score: float
    sponsored: bool = False
    sponsor_weight: float = 0.0
    meets_required_constraints: bool = True
    price_fresh: bool = True
    eligible: bool = True


def apply_sponsored_isolation(
    slots: Sequence[RankedSlot],
) -> tuple[tuple[RankedSlot, ...], tuple[str, ...]]:
    """Sponsored weight cannot break constraints, eligibility, freshness, or best label."""

    organic = [s for s in slots if not s.sponsored]
    sponsored = [s for s in slots if s.sponsored]
    reasons: list[str] = []
    safe_sponsored: list[RankedSlot] = []
    for s in sponsored:
        if not s.meets_required_constraints:
            reasons.append(f"sponsor_blocked_constraints:{s.product_id}")
            continue
        if not s.eligible:
            reasons.append(f"sponsor_blocked_ineligible:{s.product_id}")
            continue
        if not s.price_fresh:
            reasons.append(f"sponsor_blocked_stale:{s.product_id}")
            continue
        # Cap: sponsored cannot exceed best organic via weight alone for "best" path
        safe_sponsored.append(s)

    # Keep organic order; append safe sponsored marked separately (caller labels)
    merged = tuple(organic + safe_sponsored)
    return merged, tuple(reasons)


def sponsored_may_use_best_label(slot: RankedSlot) -> bool:
    return False if slot.sponsored else True


__all__ = [
    "ERROR_CLASS_OWNERS",
    "ErrorClassEvent",
    "FeedbackResultSnapshot",
    "RankedSlot",
    "ShadowComparison",
    "apply_sponsored_isolation",
    "classify_feedback_error",
    "compare_shadow",
    "sponsored_may_use_best_label",
]
