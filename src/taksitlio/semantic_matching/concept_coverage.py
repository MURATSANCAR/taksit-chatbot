"""Concept coverage scoring (ADR-008 P0.1).

When FAST emits multiple positive concepts, ranking must not collapse to
the single strongest token. Coverage is a *bonus* channel — it never
alone drives DIRECT_ALIAS auto-select and never rewards negatives or
correction-removed concepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from taksitlio.category_catalog.domain import CategorySnapshotNode
from taksitlio.semantic_matching.token_set_alias_retriever import (
    TokenSetAliasRetriever,
)
from taksitlio.semantic_matching.turkish_normalize import turkish_lower


@dataclass(frozen=True)
class ConceptCoverageScore:
    matched_positive_concept_count: int = 0
    positive_concept_coverage: float = 0.0
    weighted_concept_coverage: float = 0.0
    matched_concepts: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "matched_positive_concept_count": self.matched_positive_concept_count,
            "positive_concept_coverage": float(self.positive_concept_coverage),
            "weighted_concept_coverage": float(self.weighted_concept_coverage),
            "matched_concepts": list(self.matched_concepts),
        }


class ConceptCoverageScorer:
    """Score how many positive concepts a category node covers."""

    def __init__(
        self,
        *,
        retriever: TokenSetAliasRetriever | None = None,
        coverage_weight: float = 0.08,
    ) -> None:
        self._retriever = retriever or TokenSetAliasRetriever()
        self._coverage_weight = max(0.0, float(coverage_weight))

    def score(
        self,
        positive_concepts: Sequence[str],
        node: CategorySnapshotNode,
        *,
        excluded_concepts: Sequence[str] = (),
    ) -> ConceptCoverageScore:
        """Return coverage of ``positive_concepts`` against ``node`` aliases.

        ``excluded_concepts`` are correction-removed / negative concepts and
        never contribute to the bonus.
        """

        excluded = {turkish_lower(c) for c in excluded_concepts if c}
        usable = [
            c
            for c in positive_concepts
            if c and turkish_lower(c) not in excluded
        ]
        if not usable:
            return ConceptCoverageScore()

        matched: list[str] = []
        weight_sum = 0.0
        for concept in usable:
            hit = self._retriever.score([concept], node)
            # Any non-zero channel counts as a soft cover; surface/token-set
            # get full weight, morph/ngram get half.
            if hit.surface_exact >= 0.9 or hit.normalized_exact >= 0.9:
                matched.append(concept)
                weight_sum += 1.0
            elif hit.token_set >= 0.9 or hit.prefix_safe >= 0.8:
                matched.append(concept)
                weight_sum += 0.85
            elif hit.morphological_variant > 0 or hit.character_ngram > 0:
                matched.append(concept)
                weight_sum += 0.45

        coverage = len(matched) / max(1, len(usable))
        weighted = weight_sum / max(1.0, float(len(usable)))
        return ConceptCoverageScore(
            matched_positive_concept_count=len(matched),
            positive_concept_coverage=coverage,
            weighted_concept_coverage=weighted,
            matched_concepts=tuple(matched),
        )

    def bonus(self, coverage: ConceptCoverageScore) -> float:
        """Additive score bonus (capped). Never alone triggers auto-select."""

        if coverage.matched_positive_concept_count <= 0:
            return 0.0
        # Require at least one match; scale by weighted coverage.
        raw = self._coverage_weight * coverage.weighted_concept_coverage
        # Extra bump when ≥2 distinct positives are covered.
        if coverage.matched_positive_concept_count >= 2:
            raw += self._coverage_weight * 0.35
        return max(0.0, min(0.20, raw))


__all__ = ["ConceptCoverageScore", "ConceptCoverageScorer"]
