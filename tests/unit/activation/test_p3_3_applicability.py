"""Unit tests for P3.3 applicability + cohort policy (no DB)."""

from __future__ import annotations

from taksitlio.applicability_readiness.cohort_policy import (
    CohortMetrics,
    CohortPolicyThresholds,
    evaluate_release_cohort,
)
from taksitlio.applicability_readiness.dimensions import (
    DimensionApplicability,
    QualityDimension,
    resolve_dimension_applicability,
)
from taksitlio.runtime_flags import FeatureFlagStatus, flags_from_rows, is_internal_or_enabled


def test_brand_not_applicable_via_category_override() -> None:
    app = resolve_dimension_applicability(
        default_dimensions={"BRAND": "REQUIRED"},
        category_overrides={"42": {"BRAND": "NOT_APPLICABLE"}},
        category_id=42,
        dimension=QualityDimension.BRAND,
    )
    assert app is DimensionApplicability.NOT_APPLICABLE


def test_internal_cohort_ignores_merchant_count() -> None:
    thr = CohortPolicyThresholds.from_mapping(
        {
            "minimum_search_ready_products": 500,
            "minimum_ready_category_scopes": 1,
            "require_merchant_count": False,
            "minimum_merchant_count": 99,
        }
    )
    metrics = CohortMetrics(
        search_ready_product_count=1054,
        finance_ready_product_count=0,
        search_demand_coverage=None,
        ready_category_scope_count=10,
        merchant_count=2,
        golden_bucket_coverage=None,
        critical_error_count=0,
        projection_leakage_count=0,
    )
    d = evaluate_release_cohort(metrics, thr)
    assert d.passed
    assert "merchant_count_below_threshold" not in d.reasons


def test_internal_flag_status_roundtrip() -> None:
    flags = flags_from_rows(
        [
            {
                "flag_code": "dynamic_readiness_enabled",
                "status": "INTERNAL",
                "config": {"cohort_code": "internal_ready_merchants"},
            }
        ]
    )
    assert flags["dynamic_readiness_enabled"].status is FeatureFlagStatus.INTERNAL
    assert is_internal_or_enabled(flags, "dynamic_readiness_enabled")
