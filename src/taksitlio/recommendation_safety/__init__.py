"""Recommendation safety package (ADR-012)."""

from __future__ import annotations

from typing import Any, Sequence, Union

from taksitlio.recommendation_safety.circuit_breaker import (
    BreakerAction,
    BreakerScope,
    QualityCircuitBreaker,
    decide_breaker,
)
from taksitlio.recommendation_safety.feedback import (
    ErrorClass,
    FeedbackResultSnapshot,
    ShadowComparison,
    SponsoredPlacement,
    apply_sponsored_isolation,
    compare_shadow,
)
from taksitlio.recommendation_safety.integrity import (
    BEST_LABEL,
    NEAREST_LABEL,
    RecommendationCandidate,
    RecommendationIntegrityResult,
    RecommendationReasonCode,
    three_winners,
)
from taksitlio.recommendation_safety.integrity import (
    evaluate_recommendation_integrity as _evaluate_from_candidates,
)
from taksitlio.recommendation_safety.media_match import (
    MEDIA_PRODUCT_MATCH_UNCERTAIN,
    MediaMatchDecision,
    MediaMatchSignals,
    evaluate_media_match,
    primary_image_url,
)
from taksitlio.recommendation_safety.negative_constraints import (
    ConstraintSource,
    LockedConstraint,
    NegativeConstraintLock,
    merge_constraints_with_priority,
    source_priority,
)
from taksitlio.recommendation_safety.product_identity import (
    VariantIdentity,
    assert_finance_bound_to_offer,
    variants_compatible,
)
from taksitlio.recommendation_safety.reason_codes import (
    REASON_CODE_TEMPLATES,
    explain_reason_codes,
)
from taksitlio.recommendation_safety.recommendation import (
    LABEL_BEST,
    LABEL_NEAREST,
    IntegrityDecision,
    IntegritySignals,
    TripleWinnerSet,
    compute_triple_winners,
    why_recommended,
)
from taksitlio.recommendation_safety.recommendation import (
    evaluate_recommendation_integrity as _evaluate_from_signals,
)
from taksitlio.recommendation_safety.schema_drift import (
    DriftAction,
    DriftSignals,
    evaluate_schema_drift,
)
from taksitlio.recommendation_safety.sponsored import (
    InMemorySponsoredPlacementStore,
    PostgresSponsoredPlacementStore,
    SponsoredPlacementRecord,
)


def evaluate_recommendation_integrity(
    arg: Union[IntegritySignals, Sequence[RecommendationCandidate]],
    *args: Any,
    **kwargs: Any,
) -> Union[IntegrityDecision, RecommendationIntegrityResult]:
    """Dispatch: IntegritySignals (unit API) or RecommendationCandidate seq (acceptance)."""

    if isinstance(arg, IntegritySignals):
        return _evaluate_from_signals(arg)
    return _evaluate_from_candidates(arg, *args, **kwargs)  # type: ignore[arg-type]


ADR_SCOPE = "ADR-012"
PACKAGE_STATUS = "P0_PROD"

__all__ = [
    "ADR_SCOPE",
    "BEST_LABEL",
    "BreakerAction",
    "BreakerScope",
    "ConstraintSource",
    "DriftAction",
    "DriftSignals",
    "ErrorClass",
    "FeedbackResultSnapshot",
    "InMemorySponsoredPlacementStore",
    "IntegrityDecision",
    "IntegritySignals",
    "LABEL_BEST",
    "LABEL_NEAREST",
    "LockedConstraint",
    "MEDIA_PRODUCT_MATCH_UNCERTAIN",
    "MediaMatchDecision",
    "MediaMatchSignals",
    "NEAREST_LABEL",
    "NegativeConstraintLock",
    "PACKAGE_STATUS",
    "PostgresSponsoredPlacementStore",
    "QualityCircuitBreaker",
    "REASON_CODE_TEMPLATES",
    "RecommendationCandidate",
    "RecommendationIntegrityResult",
    "RecommendationReasonCode",
    "ShadowComparison",
    "SponsoredPlacement",
    "SponsoredPlacementRecord",
    "TripleWinnerSet",
    "VariantIdentity",
    "apply_sponsored_isolation",
    "assert_finance_bound_to_offer",
    "compare_shadow",
    "compute_triple_winners",
    "decide_breaker",
    "evaluate_media_match",
    "evaluate_recommendation_integrity",
    "evaluate_schema_drift",
    "explain_reason_codes",
    "merge_constraints_with_priority",
    "primary_image_url",
    "source_priority",
    "three_winners",
    "variants_compatible",
    "why_recommended",
]
