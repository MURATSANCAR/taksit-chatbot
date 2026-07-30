"""Hybrid scorer: weighted combination of alias/lexical/vector/use-case/hierarchy.

Weights come from the SemanticMatchPolicy; the scorer normalizes them so
callers can tune signals via the DB without breaking scale invariants.
"""

from __future__ import annotations

from dataclasses import dataclass

from taksitlio.semantic_matching.domain import (
    SemanticMatchPolicy,
    SignalBreakdown,
)


@dataclass(frozen=True)
class NormalizedWeights:
    alias: float
    lexical: float
    vector: float
    use_case: float
    hierarchy: float


class HybridScorer:
    """Deterministic weighted-sum scorer with dynamic degraded normalization."""

    def __init__(self, policy: SemanticMatchPolicy) -> None:
        self._policy = policy

    def _normalize_weights(self, degraded: bool) -> NormalizedWeights:
        alias = max(0.0, self._policy.alias_weight)
        lexical = max(0.0, self._policy.lexical_weight)
        vector = 0.0 if degraded else max(0.0, self._policy.vector_weight)
        use_case = max(0.0, self._policy.use_case_weight)
        hierarchy = max(0.0, self._policy.hierarchy_weight)
        total = alias + lexical + vector + use_case + hierarchy
        if total <= 0:
            return NormalizedWeights(0.0, 0.0, 0.0, 0.0, 0.0)
        return NormalizedWeights(
            alias=alias / total,
            lexical=lexical / total,
            vector=vector / total,
            use_case=use_case / total,
            hierarchy=hierarchy / total,
        )

    def combine(
        self,
        breakdown: SignalBreakdown,
        *,
        degraded: bool,
    ) -> float:
        weights = self._normalize_weights(degraded)
        score = (
            weights.alias * breakdown.alias
            + weights.lexical * breakdown.lexical
            + weights.vector * breakdown.vector
            + weights.use_case * breakdown.use_case
            + weights.hierarchy * breakdown.hierarchy
        )
        return max(0.0, min(1.0, score))


__all__ = ["HybridScorer", "NormalizedWeights"]
