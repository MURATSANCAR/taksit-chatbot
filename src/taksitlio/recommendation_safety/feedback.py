"""Feedback snapshots + error classes + shadow mode + sponsored (ADR-012 §22–25)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


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


# Never collapse to a single WRONG_ANSWER bucket.
FORBIDDEN_ERROR_BUCKET = "WRONG_ANSWER"


@dataclass(frozen=True)
class FeedbackResultSnapshot:
    query_version: int
    parsed_constraints: Mapping[str, Any]
    catalog_revision: Optional[str]
    price_snapshot: Optional[str]
    campaign_snapshot: Optional[str]
    selected_product: Optional[str]
    selected_bank: Optional[str]
    response_fact_ids: tuple[str, ...]
    error_class: Optional[ErrorClass] = None
    user_note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_version": self.query_version,
            "parsed_constraints": dict(self.parsed_constraints),
            "catalog_revision": self.catalog_revision,
            "price_snapshot": self.price_snapshot,
            "campaign_snapshot": self.campaign_snapshot,
            "selected_product": self.selected_product,
            "selected_bank": self.selected_bank,
            "response_fact_ids": list(self.response_fact_ids),
            "error_class": None if self.error_class is None else self.error_class.value,
            "user_note": self.user_note,
        }


@dataclass(frozen=True)
class ShadowComparison:
    shadow_payload: Mapping[str, Any]
    live_payload: Mapping[str, Any]
    diffs: tuple[str, ...]
    shown_to_user: bool = False


def compare_shadow(
    live: Mapping[str, Any],
    shadow: Mapping[str, Any],
    *,
    keys: Sequence[str] = ("product_ids", "ranking_labels", "monthly_payments"),
) -> ShadowComparison:
    diffs: list[str] = []
    for key in keys:
        if live.get(key) != shadow.get(key):
            diffs.append(key)
    return ShadowComparison(
        shadow_payload=dict(shadow),
        live_payload=dict(live),
        diffs=tuple(diffs),
        shown_to_user=False,
    )


@dataclass(frozen=True)
class SponsoredPlacement:
    product_id: str
    weight: float = 0.0


def apply_sponsored_isolation(
    organic_order: Sequence[str],
    sponsored: Sequence[SponsoredPlacement],
    *,
    eligible_ids: Optional[set[str]] = None,
    stale_ids: Optional[set[str]] = None,
    best_label_ids: Optional[set[str]] = None,
) -> tuple[str, ...]:
    """Sponsored weight cannot violate constraints or steal 'en uygun'."""

    eligible = eligible_ids or set(organic_order)
    stale = stale_ids or set()
    best = best_label_ids or set()
    organic = list(organic_order)
    for sp in sorted(sponsored, key=lambda s: s.weight, reverse=True):
        if sp.product_id not in eligible:
            continue
        if sp.product_id in stale:
            continue
        if sp.product_id in best:
            # May appear as sponsored slot but not as organic best
            continue
        if sp.product_id in organic:
            organic.remove(sp.product_id)
        # Insert after organic top (index 1) as sponsored — never index 0 best
        insert_at = min(1, len(organic))
        organic.insert(insert_at, sp.product_id)
    return tuple(organic)


__all__ = [
    "ErrorClass",
    "FORBIDDEN_ERROR_BUCKET",
    "FeedbackResultSnapshot",
    "ShadowComparison",
    "SponsoredPlacement",
    "apply_sponsored_isolation",
    "compare_shadow",
]
