"""Source-taxonomy → canonical category mapping candidates (merchant-scoped)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from taksitlio.continuous_learning.lifecycle import (
    LearningCandidateView,
    LearningStatus,
    PromotionThresholds,
    assert_not_direct_promoted,
    evaluate_promotion_gate,
)


@dataclass(frozen=True)
class SourceTaxonomyNodeRef:
    merchant_id: int
    source_taxonomy_id: int
    source_node_id: int
    path: str
    normalized_path: str


@dataclass(frozen=True)
class TaxonomyMappingEvidenceItem:
    evidence_type: str
    polarity: str = "POSITIVE"
    weight: float = 1.0
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass
class TaxonomyMappingCandidate:
    node: SourceTaxonomyNodeRef
    candidate_category_id: int
    learning_status: LearningStatus = LearningStatus.OBSERVED
    confidence: float = 0.0
    candidate_gap: float = 0.0
    evidence_score: float = 0.0
    sample_consistency: float = 0.0
    conflict_count: int = 0
    observation_count: int = 0
    match_method: str = "UNKNOWN"
    evidence_items: list[TaxonomyMappingEvidenceItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        assert_not_direct_promoted(self.learning_status)


def score_taxonomy_candidate(
    *,
    exact_mapping_hit: bool,
    normalized_alias_hit: bool,
    sibling_parent_agreement: float,
    sample_title_consistency: float,
    brand_distribution_consistency: float,
    negative_category_conflicts: int,
    historic_stability: float,
) -> tuple[float, float, float]:
    """Return (confidence, candidate_gap_proxy, sample_consistency)."""

    parts: list[float] = []
    if exact_mapping_hit:
        parts.append(0.98)
    if normalized_alias_hit:
        parts.append(0.90)
    parts.append(0.7 * sibling_parent_agreement)
    parts.append(0.8 * sample_title_consistency)
    parts.append(0.6 * brand_distribution_consistency)
    parts.append(0.5 * historic_stability)
    confidence = min(0.99, sum(parts) / max(len(parts), 1))
    if negative_category_conflicts:
        confidence *= max(0.0, 1.0 - 0.25 * negative_category_conflicts)
    sample_consistency = (
        sample_title_consistency + brand_distribution_consistency + sibling_parent_agreement
    ) / 3.0
    # gap vs hypothetical runner-up approximated from consistency spread
    gap = abs(sample_title_consistency - (1.0 - sample_title_consistency)) * 0.5
    gap = max(gap, confidence - 0.7)
    return confidence, gap, sample_consistency


def create_candidate(
    node: SourceTaxonomyNodeRef,
    candidate_category_id: int,
    *,
    match_method: str,
    confidence: float,
    candidate_gap: float,
    sample_consistency: float,
    conflict_count: int,
    evidence: Sequence[TaxonomyMappingEvidenceItem],
) -> TaxonomyMappingCandidate:
    status = LearningStatus.CANDIDATE if confidence >= 0.5 else LearningStatus.OBSERVED
    cand = TaxonomyMappingCandidate(
        node=node,
        candidate_category_id=candidate_category_id,
        learning_status=status,
        confidence=confidence,
        candidate_gap=candidate_gap,
        sample_consistency=sample_consistency,
        conflict_count=conflict_count,
        observation_count=len(evidence),
        match_method=match_method,
        evidence_items=list(evidence),
        evidence_score=sum(e.weight for e in evidence if e.polarity == "POSITIVE"),
    )
    return cand


def can_auto_publish(
    candidate: TaxonomyMappingCandidate,
    thresholds: PromotionThresholds,
) -> tuple[bool, tuple[str, ...]]:
    """High confidence + gap + no conflict required; still needs SHADOW path."""

    reasons: list[str] = []
    if candidate.conflict_count > thresholds.maximum_conflict_count:
        reasons.append("conflict_present")
    if candidate.confidence < thresholds.minimum_confidence:
        reasons.append("confidence_below_threshold")
    if candidate.candidate_gap < thresholds.minimum_candidate_gap:
        reasons.append("candidate_gap_too_small")
    if candidate.sample_consistency < thresholds.minimum_sample_consistency:
        reasons.append("sample_consistency_below_threshold")
    if candidate.observation_count < thresholds.minimum_observations:
        reasons.append("insufficient_observations")
    if reasons:
        return False, tuple(reasons)

    # Advance through SHADOW then PROMOTED via lifecycle gate
    view = LearningCandidateView(
        learning_status=LearningStatus.SHADOW,
        confidence=candidate.confidence,
        candidate_gap=candidate.candidate_gap,
        observation_count=candidate.observation_count,
        positive_evidence=candidate.observation_count,
        negative_evidence=0,
        conflict_count=candidate.conflict_count,
        sample_consistency=candidate.sample_consistency,
    )
    decision = evaluate_promotion_gate(
        view, thresholds, target=LearningStatus.PROMOTED
    )
    return decision.allowed, decision.reasons


def merchant_scoped_key(merchant_id: int, source_taxonomy_id: int, source_node_id: int) -> str:
    """Merchant difference is data scope — never global category-name equality."""

    return f"{merchant_id}:{source_taxonomy_id}:{source_node_id}"


__all__ = [
    "SourceTaxonomyNodeRef",
    "TaxonomyMappingCandidate",
    "TaxonomyMappingEvidenceItem",
    "can_auto_publish",
    "create_candidate",
    "merchant_scoped_key",
    "score_taxonomy_candidate",
]
