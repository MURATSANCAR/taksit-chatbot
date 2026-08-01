"""Continuous learning package — controlled, versioned, event-driven adaptation."""

from taksitlio.continuous_learning.alias import (
    AliasCandidateState,
    AliasObservation,
    maybe_advance,
    observe_alias,
    record_user_correction,
)
from taksitlio.continuous_learning.attributes import (
    NumericExtraction,
    validate_numeric_extraction,
)
from taksitlio.continuous_learning.drift import (
    DriftAlarm,
    DriftType,
    detect_taxonomy_drift,
)
from taksitlio.continuous_learning.lifecycle import (
    LearningStatus,
    PromotionThresholds,
    TenantLearningScope,
    evaluate_promotion_gate,
)
from taksitlio.continuous_learning.taxonomy import (
    TaxonomyMappingCandidate,
    can_auto_publish,
    score_taxonomy_candidate,
)

__all__ = [
    "AliasCandidateState",
    "AliasObservation",
    "DriftAlarm",
    "DriftType",
    "LearningStatus",
    "NumericExtraction",
    "PromotionThresholds",
    "TaxonomyMappingCandidate",
    "TenantLearningScope",
    "can_auto_publish",
    "detect_taxonomy_drift",
    "evaluate_promotion_gate",
    "maybe_advance",
    "observe_alias",
    "record_user_correction",
    "score_taxonomy_candidate",
    "validate_numeric_extraction",
]
