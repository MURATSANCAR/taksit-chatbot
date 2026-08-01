"""Alias learning from anonymized query observations (controlled promotion)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from taksitlio.continuous_learning.lifecycle import (
    LearningCandidateView,
    LearningStatus,
    PromotionThresholds,
    TenantLearningScope,
    apply_correction_evidence,
    assert_not_direct_promoted,
    evaluate_promotion_gate,
    global_learning_allowed,
)


@dataclass(frozen=True)
class AliasObservation:
    raw_token: str
    normalized_token: str
    entity_type: str
    resolved_entity_id: str
    resolution_confidence: float
    top1_top2_gap: float
    match_method: str
    context: str
    tenant_scope: TenantLearningScope = TenantLearningScope.GLOBAL_ENTITY_LEARNING
    anonymized: bool = True
    signal_type: Optional[str] = None
    source: str = "EVIDENCE"  # or LLM_INFERENCE


@dataclass
class AliasCandidateState:
    entity_type: str
    observed_alias: str
    normalized_alias: str
    candidate_entity_id: str
    learning_status: LearningStatus = LearningStatus.OBSERVED
    confidence: float = 0.0
    candidate_gap: float = 0.0
    observation_count: int = 0
    positive_evidence: int = 0
    negative_evidence: int = 0
    match_method: str = "UNKNOWN"
    context: str = ""
    evidence: dict[str, object] = field(default_factory=dict)
    source: str = "EVIDENCE"

    def __post_init__(self) -> None:
        assert_not_direct_promoted(self.learning_status)


def observe_alias(
    state: Optional[AliasCandidateState],
    observation: AliasObservation,
) -> Optional[AliasCandidateState]:
    """Accumulate evidence; never promote on a single observation."""

    if not global_learning_allowed(observation.tenant_scope, anonymized=observation.anonymized):
        return state
    if observation.source == "LLM_INFERENCE" and observation.resolution_confidence < 0.99:
        # LLM semantic candidates stay observations only
        pass

    if state is None:
        state = AliasCandidateState(
            entity_type=observation.entity_type,
            observed_alias=observation.raw_token,
            normalized_alias=observation.normalized_token,
            candidate_entity_id=observation.resolved_entity_id,
            learning_status=LearningStatus.OBSERVED,
            confidence=observation.resolution_confidence,
            candidate_gap=observation.top1_top2_gap,
            observation_count=1,
            positive_evidence=1,
            match_method=observation.match_method,
            context=observation.context,
            source=observation.source,
            evidence={"first_method": observation.match_method},
        )
        return state

    if state.candidate_entity_id != observation.resolved_entity_id:
        state.negative_evidence += 1
        state.observation_count += 1
        return state

    state.observation_count += 1
    state.positive_evidence += 1
    state.confidence = max(state.confidence, observation.resolution_confidence)
    state.candidate_gap = max(state.candidate_gap, observation.top1_top2_gap)
    if state.learning_status is LearningStatus.OBSERVED and state.observation_count >= 2:
        state.learning_status = LearningStatus.CANDIDATE
    return state


def record_user_correction(
    *,
    rejected_state: Optional[AliasCandidateState],
    confirmed_state: Optional[AliasCandidateState],
    rejected_entity_id: str,
    confirmed_entity_id: str,
) -> tuple[Optional[AliasCandidateState], Optional[AliasCandidateState]]:
    neg, pos = apply_correction_evidence(
        first_resolution_entity_id=rejected_entity_id,
        second_resolution_entity_id=confirmed_entity_id,
        first_candidate=None,
        second_candidate=None,
    )
    if rejected_state is not None and neg.startswith("NEGATIVE"):
        rejected_state.negative_evidence += 1
        rejected_state.observation_count += 1
        if rejected_state.negative_evidence > rejected_state.positive_evidence:
            rejected_state.learning_status = LearningStatus.REJECTED
    if confirmed_state is not None and pos.startswith("POSITIVE"):
        confirmed_state.positive_evidence += 1
        confirmed_state.observation_count += 1
        if confirmed_state.learning_status is LearningStatus.OBSERVED:
            confirmed_state.learning_status = LearningStatus.CANDIDATE
    return rejected_state, confirmed_state


def maybe_advance(
    state: AliasCandidateState,
    thresholds: PromotionThresholds,
    *,
    target: LearningStatus,
) -> tuple[AliasCandidateState, tuple[str, ...]]:
    view = LearningCandidateView(
        learning_status=state.learning_status,
        confidence=state.confidence,
        candidate_gap=state.candidate_gap,
        observation_count=state.observation_count,
        positive_evidence=state.positive_evidence,
        negative_evidence=state.negative_evidence,
        source=state.source,
    )
    decision = evaluate_promotion_gate(view, thresholds, target=target)
    if decision.allowed:
        state.learning_status = target
    return state, decision.reasons


def thresholds_from_policy(payload: Mapping[str, object]) -> PromotionThresholds:
    return PromotionThresholds.from_mapping(payload)


__all__ = [
    "AliasCandidateState",
    "AliasObservation",
    "maybe_advance",
    "observe_alias",
    "record_user_correction",
    "thresholds_from_policy",
]
