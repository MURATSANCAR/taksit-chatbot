"""Versioned release cohort policy evaluation (no static merchant-count gate)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class CohortPolicyThresholds:
    minimum_search_ready_products: int = 500
    minimum_finance_ready_products: int = 0
    minimum_search_demand_coverage: float = 0.0
    minimum_ready_category_scopes: int = 1
    minimum_golden_bucket_coverage: float = 0.0
    maximum_critical_errors: int = 0
    maximum_projection_leakage: int = 0
    maximum_wrong_mapping: int = 0
    require_merchant_count: bool = False
    minimum_merchant_count: int = 0

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "CohortPolicyThresholds":
        return cls(
            minimum_search_ready_products=int(
                data.get("minimum_search_ready_products", 500) or 500
            ),
            minimum_finance_ready_products=int(
                data.get("minimum_finance_ready_products", 0) or 0
            ),
            minimum_search_demand_coverage=float(
                data.get("minimum_search_demand_coverage", 0.0) or 0.0
            ),
            minimum_ready_category_scopes=int(
                data.get("minimum_ready_category_scopes", 1) or 1
            ),
            minimum_golden_bucket_coverage=float(
                data.get("minimum_golden_bucket_coverage", 0.0) or 0.0
            ),
            maximum_critical_errors=int(data.get("maximum_critical_errors", 0) or 0),
            maximum_projection_leakage=int(
                data.get("maximum_projection_leakage", 0) or 0
            ),
            maximum_wrong_mapping=int(data.get("maximum_wrong_mapping", 0) or 0),
            require_merchant_count=bool(data.get("require_merchant_count", False)),
            minimum_merchant_count=int(data.get("minimum_merchant_count", 0) or 0),
        )


@dataclass(frozen=True)
class CohortMetrics:
    search_ready_product_count: int
    finance_ready_product_count: int
    search_demand_coverage: Optional[float]
    ready_category_scope_count: int
    merchant_count: int
    golden_bucket_coverage: Optional[float]
    critical_error_count: int
    projection_leakage_count: int
    wrong_mapping_count: int = 0


@dataclass(frozen=True)
class CohortPolicyDecision:
    passed: bool
    reasons: tuple[str, ...]


def evaluate_release_cohort(
    metrics: CohortMetrics, thresholds: CohortPolicyThresholds
) -> CohortPolicyDecision:
    reasons: list[str] = []
    if metrics.search_ready_product_count < thresholds.minimum_search_ready_products:
        reasons.append("search_ready_products_below_threshold")
    if metrics.finance_ready_product_count < thresholds.minimum_finance_ready_products:
        reasons.append("finance_ready_products_below_threshold")
    if metrics.ready_category_scope_count < thresholds.minimum_ready_category_scopes:
        reasons.append("ready_category_scopes_below_threshold")
    if metrics.critical_error_count > thresholds.maximum_critical_errors:
        reasons.append("critical_errors_exceeded")
    if metrics.projection_leakage_count > thresholds.maximum_projection_leakage:
        reasons.append("projection_leakage_exceeded")
    if metrics.wrong_mapping_count > thresholds.maximum_wrong_mapping:
        reasons.append("wrong_mapping_exceeded")
    if metrics.search_demand_coverage is not None and (
        metrics.search_demand_coverage < thresholds.minimum_search_demand_coverage
    ):
        reasons.append("search_demand_coverage_below_threshold")
    if metrics.golden_bucket_coverage is not None and (
        metrics.golden_bucket_coverage < thresholds.minimum_golden_bucket_coverage
    ):
        reasons.append("golden_bucket_coverage_below_threshold")
    # Explicitly optional: merchant count is reporting-only unless policy requires it.
    if thresholds.require_merchant_count and (
        metrics.merchant_count < thresholds.minimum_merchant_count
    ):
        reasons.append("merchant_count_below_threshold")
    return CohortPolicyDecision(passed=not reasons, reasons=tuple(reasons))


__all__ = [
    "CohortMetrics",
    "CohortPolicyDecision",
    "CohortPolicyThresholds",
    "evaluate_release_cohort",
]
