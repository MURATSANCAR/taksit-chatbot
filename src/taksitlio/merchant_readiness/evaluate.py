"""Merchant readiness evaluation from versioned policy thresholds (P2-LIVE)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence

from taksitlio.merchant.models import MerchantActivationGate


class MerchantReadinessStatus(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class ReadinessThresholds:
    minimum_searchable_products: int = 50
    minimum_category_coverage: float = 0.95
    minimum_brand_coverage: float = 0.90
    minimum_critical_attribute_coverage: float = 0.90
    minimum_card_media_coverage: float = 0.95
    minimum_fresh_price_coverage: float = 0.95
    minimum_valid_url_coverage: float = 0.99
    maximum_critical_error: int = 0
    minimum_golden_pass_rate: float = 1.0
    wrong_mapping_tolerance: int = 0
    payment_calculation_error_tolerance: int = 0
    # Fleet / scope quality (merchant readiness closeout gates; not per-SKU invent)
    minimum_total_ready_products: int = 500
    minimum_search_demand_coverage: float = 0.0
    minimum_medium_or_high_volume_merchant_count: int = 1
    minimum_finance_ready_products: int = 0
    medium_high_volume_product_floor: int = 200

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ReadinessThresholds":
        return cls(
            minimum_searchable_products=int(
                data.get("minimum_searchable_products", 50) or 50
            ),
            minimum_category_coverage=float(
                data.get("minimum_category_coverage", 0.95) or 0.95
            ),
            minimum_brand_coverage=float(data.get("minimum_brand_coverage", 0.90) or 0.90),
            minimum_critical_attribute_coverage=float(
                data.get("minimum_critical_attribute_coverage", 0.90) or 0.90
            ),
            minimum_card_media_coverage=float(
                data.get("minimum_card_media_coverage", 0.95) or 0.95
            ),
            minimum_fresh_price_coverage=float(
                data.get("minimum_fresh_price_coverage", 0.95) or 0.95
            ),
            minimum_valid_url_coverage=float(
                data.get("minimum_valid_url_coverage", 0.99) or 0.99
            ),
            maximum_critical_error=int(data.get("maximum_critical_error", 0) or 0),
            minimum_golden_pass_rate=float(
                data.get("minimum_golden_pass_rate", 1.0) or 1.0
            ),
            wrong_mapping_tolerance=int(data.get("wrong_mapping_tolerance", 0) or 0),
            payment_calculation_error_tolerance=int(
                data.get("payment_calculation_error_tolerance", 0) or 0
            ),
            minimum_total_ready_products=int(
                data.get("minimum_total_ready_products", 500) or 500
            ),
            minimum_search_demand_coverage=float(
                data.get("minimum_search_demand_coverage", 0.0) or 0.0
            ),
            minimum_medium_or_high_volume_merchant_count=int(
                data.get("minimum_medium_or_high_volume_merchant_count", 1) or 1
            ),
            minimum_finance_ready_products=int(
                data.get("minimum_finance_ready_products", 0) or 0
            ),
            medium_high_volume_product_floor=int(
                data.get("medium_high_volume_product_floor", 200) or 200
            ),
        )


@dataclass(frozen=True)
class MerchantCoverageMetrics:
    active_products: int
    searchable_products: int
    category_coverage: float
    brand_coverage: float
    attribute_coverage: float
    stock_coverage: float
    card_media_coverage: float
    fresh_price_coverage: float
    valid_url_coverage: float
    finance_coverage: float
    payment_plan_coverage: float
    golden_pass_rate: Optional[float] = None
    critical_error_count: int = 0
    wrong_mapping_count: int = 0
    payment_calculation_error_count: int = 0
    source_healthy: bool = True
    merchant_verified: bool = True
    manually_disabled: bool = False


@dataclass(frozen=True)
class MerchantReadinessDecision:
    status: MerchantReadinessStatus
    previous_status: Optional[MerchantReadinessStatus]
    reasons: tuple[str, ...]
    include_in_search: bool
    activation_gate: MerchantActivationGate


def _fail_reasons(
    metrics: MerchantCoverageMetrics, thresholds: ReadinessThresholds
) -> list[str]:
    reasons: list[str] = []
    if metrics.manually_disabled:
        reasons.append("manually_disabled")
    if not metrics.merchant_verified:
        reasons.append("merchant_unverified")
    if not metrics.source_healthy:
        reasons.append("source_unhealthy")
    if metrics.active_products < thresholds.minimum_searchable_products:
        reasons.append("insufficient_searchable_products")
    if metrics.category_coverage < thresholds.minimum_category_coverage:
        reasons.append("category_coverage_below_threshold")
    if metrics.brand_coverage < thresholds.minimum_brand_coverage:
        reasons.append("brand_coverage_below_threshold")
    if metrics.attribute_coverage < thresholds.minimum_critical_attribute_coverage:
        reasons.append("attribute_coverage_below_threshold")
    if metrics.card_media_coverage < thresholds.minimum_card_media_coverage:
        reasons.append("card_media_coverage_below_threshold")
    if metrics.fresh_price_coverage < thresholds.minimum_fresh_price_coverage:
        reasons.append("fresh_price_coverage_below_threshold")
    if metrics.valid_url_coverage < thresholds.minimum_valid_url_coverage:
        reasons.append("valid_url_coverage_below_threshold")
    if metrics.critical_error_count > thresholds.maximum_critical_error:
        reasons.append("critical_errors_exceeded")
    if metrics.wrong_mapping_count > thresholds.wrong_mapping_tolerance:
        reasons.append("wrong_mapping_detected")
    if (
        metrics.payment_calculation_error_count
        > thresholds.payment_calculation_error_tolerance
    ):
        reasons.append("payment_calculation_error")
    if metrics.golden_pass_rate is not None and (
        metrics.golden_pass_rate < thresholds.minimum_golden_pass_rate
    ):
        reasons.append("golden_pass_rate_below_threshold")
    return reasons


def evaluate_merchant_readiness(
    metrics: MerchantCoverageMetrics,
    thresholds: ReadinessThresholds,
    *,
    previous_status: Optional[MerchantReadinessStatus] = None,
) -> MerchantReadinessDecision:
    """Policy-driven READY / PARTIAL / BLOCKED / DEGRADED / DISABLED."""

    if metrics.manually_disabled:
        return MerchantReadinessDecision(
            status=MerchantReadinessStatus.DISABLED,
            previous_status=previous_status,
            reasons=("manually_disabled",),
            include_in_search=False,
            activation_gate=MerchantActivationGate.BLOCKED,
        )

    reasons = _fail_reasons(metrics, thresholds)
    hard_block = {
        "merchant_unverified",
        "source_unhealthy",
        "wrong_mapping_detected",
        "payment_calculation_error",
        "critical_errors_exceeded",
    }
    if hard_block.intersection(reasons):
        status = MerchantReadinessStatus.BLOCKED
    elif not reasons:
        status = MerchantReadinessStatus.READY
    elif previous_status is MerchantReadinessStatus.READY:
        # Auto degrade when a previously READY merchant slips
        status = MerchantReadinessStatus.DEGRADED
    elif metrics.searchable_products > 0 and metrics.category_coverage >= 0.5:
        status = MerchantReadinessStatus.PARTIAL
    else:
        status = MerchantReadinessStatus.BLOCKED

    include = status in {
        MerchantReadinessStatus.READY,
        MerchantReadinessStatus.PARTIAL,
    }
    gate_map = {
        MerchantReadinessStatus.READY: MerchantActivationGate.READY,
        MerchantReadinessStatus.PARTIAL: MerchantActivationGate.PARTIAL,
        MerchantReadinessStatus.BLOCKED: MerchantActivationGate.BLOCKED,
        MerchantReadinessStatus.DEGRADED: MerchantActivationGate.BLOCKED,
        MerchantReadinessStatus.DISABLED: MerchantActivationGate.BLOCKED,
    }
    return MerchantReadinessDecision(
        status=status,
        previous_status=previous_status,
        reasons=tuple(reasons),
        include_in_search=include and status is not MerchantReadinessStatus.DEGRADED,
        activation_gate=gate_map[status],
    )


def recover_from_degraded(
    metrics: MerchantCoverageMetrics,
    thresholds: ReadinessThresholds,
) -> MerchantReadinessDecision:
    """DEGRADED → requires full READY thresholds again (shadow validation external)."""

    decision = evaluate_merchant_readiness(
        metrics, thresholds, previous_status=MerchantReadinessStatus.DEGRADED
    )
    if decision.status is MerchantReadinessStatus.READY:
        return decision
    # Stay degraded until fully ready
    return MerchantReadinessDecision(
        status=MerchantReadinessStatus.DEGRADED,
        previous_status=MerchantReadinessStatus.DEGRADED,
        reasons=decision.reasons or ("awaiting_full_recovery",),
        include_in_search=False,
        activation_gate=MerchantActivationGate.BLOCKED,
    )


def compute_release_scope_rows(
    decisions: Sequence[tuple[int, MerchantReadinessDecision]],
    *,
    catalog_revision: str,
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for merchant_id, decision in decisions:
        rows.append(
            {
                "catalog_revision": catalog_revision,
                "merchant_id": merchant_id,
                "include_in_search": decision.include_in_search
                and decision.status is MerchantReadinessStatus.READY,
                "readiness_status": decision.status.value,
                "reasons": list(decision.reasons),
            }
        )
    return tuple(rows)


__all__ = [
    "MerchantCoverageMetrics",
    "MerchantReadinessDecision",
    "MerchantReadinessStatus",
    "ReadinessThresholds",
    "compute_release_scope_rows",
    "evaluate_merchant_readiness",
    "recover_from_degraded",
]
