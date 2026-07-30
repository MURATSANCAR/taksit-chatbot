"""Decision policy that converts a candidate list into a typed match status."""

from __future__ import annotations

from typing import Sequence

from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    CategoryMatchDecision,
    CategoryMatchStatus,
    SemanticMatchPolicy,
)


class DecisionPolicy:
    def __init__(self, policy: SemanticMatchPolicy) -> None:
        self._policy = policy

    def decide(
        self,
        candidates: Sequence[CategoryCandidate],
    ) -> CategoryMatchDecision:
        if not candidates:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=None,
                reason="no candidates above minimum_score",
            )
        top = candidates[0]
        if top.score < self._policy.minimum_score:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=None,
                reason=f"top score {top.score:.3f} below minimum_score",
            )
        if len(candidates) == 1:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.MATCHED,
                selected_category_id=top.category_id,
                score_gap=None,
            )
        second = candidates[1]
        gap = top.score - second.score
        if gap <= self._policy.clarify_score_gap:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason=(
                    f"score gap {gap:.3f} within clarify_score_gap="
                    f"{self._policy.clarify_score_gap:.3f}"
                ),
            )
        return CategoryMatchDecision(
            status=CategoryMatchStatus.MATCHED,
            selected_category_id=top.category_id,
            score_gap=gap,
        )


__all__ = ["DecisionPolicy"]
