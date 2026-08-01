"""Applicability-aware readiness and release cohorts (P3.3)."""

from __future__ import annotations

from taksitlio.applicability_readiness.cohort_policy import (
    CohortPolicyThresholds,
    CohortMetrics,
    evaluate_release_cohort,
)
from taksitlio.applicability_readiness.dimensions import (
    DimensionApplicability,
    QualityDimension,
    resolve_dimension_applicability,
)

__all__ = [
    "CohortMetrics",
    "CohortPolicyThresholds",
    "DimensionApplicability",
    "QualityDimension",
    "evaluate_release_cohort",
    "resolve_dimension_applicability",
]
