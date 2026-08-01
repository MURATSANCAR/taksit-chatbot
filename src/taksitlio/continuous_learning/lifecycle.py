"""Controlled continuous learning lifecycle (Recovery P2-LIVE).

Never create learned records directly as PROMOTED.
Uncontrolled online self-training is forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional


class LearningStatus(str, Enum):
    OBSERVED = "OBSERVED"
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    SHADOW = "SHADOW"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class TenantLearningScope(str, Enum):
    USER_PREFERENCE_MEMORY = "USER_PREFERENCE_MEMORY"
    TENANT_PREFERENCE = "TENANT_PREFERENCE"
    GLOBAL_ENTITY_LEARNING = "GLOBAL_ENTITY_LEARNING"


_FORWARD = {
    LearningStatus.OBSERVED: {LearningStatus.CANDIDATE, LearningStatus.REJECTED},
    LearningStatus.CANDIDATE: {
        LearningStatus.VALIDATED,
        LearningStatus.REJECTED,
        LearningStatus.OBSERVED,
    },
    LearningStatus.VALIDATED: {
        LearningStatus.SHADOW,
        LearningStatus.REJECTED,
        LearningStatus.CANDIDATE,
    },
    LearningStatus.SHADOW: {
        LearningStatus.PROMOTED,
        LearningStatus.REJECTED,
        LearningStatus.VALIDATED,
    },
    LearningStatus.PROMOTED: {LearningStatus.ROLLED_BACK},
    LearningStatus.REJECTED: {LearningStatus.CANDIDATE},
    LearningStatus.ROLLED_BACK: {LearningStatus.CANDIDATE},
}


@dataclass(frozen=True)
class PromotionThresholds:
    minimum_observations: int = 5
    minimum_confidence: float = 0.85
    minimum_candidate_gap: float = 0.15
    minimum_positive_minus_negative: int = 3
    minimum_sample_consistency: float = 0.0
    maximum_conflict_count: int = 0
    maximum_conflict_ratio: float = 1.0
    allow_single_observation_promote: bool = False
    require_shadow_before_promote: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "PromotionThresholds":
        return cls(
            minimum_observations=int(data.get("minimum_observations", 5) or 5),
            minimum_confidence=float(data.get("minimum_confidence", 0.85) or 0.85),
            minimum_candidate_gap=float(data.get("minimum_candidate_gap", 0.15) or 0.15),
            minimum_positive_minus_negative=int(
                data.get("minimum_positive_minus_negative", 3) or 3
            ),
            minimum_sample_consistency=float(
                data.get("minimum_sample_consistency", 0.0) or 0.0
            ),
            maximum_conflict_count=int(data.get("maximum_conflict_count", 0) or 0),
            maximum_conflict_ratio=float(data.get("maximum_conflict_ratio", 1.0) or 1.0),
            allow_single_observation_promote=bool(
                data.get("allow_single_observation_promote", False)
            ),
            require_shadow_before_promote=bool(
                data.get("require_shadow_before_promote", True)
            ),
        )


@dataclass(frozen=True)
class LearningCandidateView:
    learning_status: LearningStatus
    confidence: float
    candidate_gap: float
    observation_count: int
    positive_evidence: int = 0
    negative_evidence: int = 0
    conflict_count: int = 0
    sample_consistency: float = 1.0
    source: str = "EVIDENCE"  # LLM_INFERENCE is lower priority


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    from_status: LearningStatus
    to_status: LearningStatus
    reasons: tuple[str, ...]


def can_transition(current: LearningStatus, target: LearningStatus) -> bool:
    if current is target:
        return False
    return target in _FORWARD.get(current, set())


def assert_not_direct_promoted(initial: LearningStatus) -> None:
    if initial is LearningStatus.PROMOTED:
        raise ValueError(
            "Learned records must not be created directly as PROMOTED; "
            "start at OBSERVED/CANDIDATE"
        )


def evaluate_promotion_gate(
    candidate: LearningCandidateView,
    thresholds: PromotionThresholds,
    *,
    target: LearningStatus = LearningStatus.PROMOTED,
) -> TransitionDecision:
    """Gate for VALIDATED→SHADOW and SHADOW→PROMOTED."""

    reasons: list[str] = []
    if not can_transition(candidate.learning_status, target):
        return TransitionDecision(
            False,
            candidate.learning_status,
            target,
            ("invalid_transition",),
        )

    if target is LearningStatus.PROMOTED:
        if (
            thresholds.require_shadow_before_promote
            and candidate.learning_status is not LearningStatus.SHADOW
        ):
            reasons.append("shadow_required_before_promote")
        if candidate.observation_count < thresholds.minimum_observations:
            reasons.append("insufficient_observations")
        if (
            not thresholds.allow_single_observation_promote
            and candidate.observation_count < 2
        ):
            reasons.append("single_observation_promotion_forbidden")
        if candidate.confidence < thresholds.minimum_confidence:
            reasons.append("confidence_below_threshold")
        if candidate.candidate_gap < thresholds.minimum_candidate_gap:
            reasons.append("candidate_gap_too_small")
        net = candidate.positive_evidence - candidate.negative_evidence
        if net < thresholds.minimum_positive_minus_negative:
            reasons.append("insufficient_net_positive_evidence")
        if candidate.conflict_count > thresholds.maximum_conflict_count:
            reasons.append("conflict_count_exceeded")
        total = max(candidate.observation_count, 1)
        if (candidate.conflict_count / total) > thresholds.maximum_conflict_ratio:
            reasons.append("conflict_ratio_exceeded")
        if candidate.sample_consistency < thresholds.minimum_sample_consistency:
            reasons.append("sample_consistency_below_threshold")
        if candidate.source == "LLM_INFERENCE":
            reasons.append("llm_inference_cannot_auto_promote")

    if target is LearningStatus.SHADOW:
        if candidate.learning_status is not LearningStatus.VALIDATED:
            reasons.append("validated_required_before_shadow")
        if candidate.confidence < thresholds.minimum_confidence:
            reasons.append("confidence_below_threshold")
        if candidate.observation_count < max(2, thresholds.minimum_observations // 2):
            reasons.append("insufficient_observations_for_shadow")

    return TransitionDecision(
        allowed=not reasons,
        from_status=candidate.learning_status,
        to_status=target,
        reasons=tuple(reasons),
    )


def apply_correction_evidence(
    *,
    first_resolution_entity_id: str,
    second_resolution_entity_id: str,
    first_candidate: Optional[LearningCandidateView],
    second_candidate: Optional[LearningCandidateView],
) -> tuple[str, str]:
    """User correction: negative for first, positive for second. No auto-promote."""

    if first_resolution_entity_id == second_resolution_entity_id:
        return ("noop_same_entity", "noop_same_entity")
    _ = first_candidate, second_candidate
    return (
        f"NEGATIVE:{first_resolution_entity_id}",
        f"POSITIVE:{second_resolution_entity_id}",
    )


def global_learning_allowed(scope: TenantLearningScope, *, anonymized: bool) -> bool:
    if scope is TenantLearningScope.GLOBAL_ENTITY_LEARNING:
        return anonymized
    return False


__all__ = [
    "LearningCandidateView",
    "LearningStatus",
    "PromotionThresholds",
    "TenantLearningScope",
    "TransitionDecision",
    "apply_correction_evidence",
    "assert_not_direct_promoted",
    "can_transition",
    "evaluate_promotion_gate",
    "global_learning_allowed",
]
