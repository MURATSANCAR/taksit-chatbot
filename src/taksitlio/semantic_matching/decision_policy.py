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
        *,
        degraded: bool = False,
    ) -> CategoryMatchDecision:
        eligible = [
            c
            for c in candidates
            if c.score >= self._policy.minimum_candidate_score
        ]
        if not eligible:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=None,
                reason="no candidates above minimum_candidate_score",
                reason_code="BELOW_MINIMUM_CANDIDATE_SCORE",
            )

        top = eligible[0]
        gap = None
        if len(eligible) >= 2:
            gap = top.score - eligible[1].score

        if degraded:
            exact = _is_strong_exact_alias(top)
            if (
                exact
                and self._policy.exact_alias_can_auto_select
                and top.score >= self._policy.minimum_auto_select_score
                and (gap is None or gap > self._policy.minimum_auto_select_gap)
            ):
                return CategoryMatchDecision(
                    status=CategoryMatchStatus.MATCHED,
                    selected_category_id=top.category_id,
                    score_gap=gap,
                    reason="degraded exact alias auto-select",
                    reason_code="DEGRADED_EXACT_ALIAS",
                )
            if gap is not None and gap <= self._policy.minimum_auto_select_gap:
                return CategoryMatchDecision(
                    status=CategoryMatchStatus.AMBIGUOUS,
                    selected_category_id=None,
                    score_gap=gap,
                    reason="degraded ambiguous candidates",
                    reason_code="TOP_SCORE_GAP_TOO_SMALL",
                    missing_concepts=("product_form",),
                )
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS
                if top.score >= self._policy.minimum_candidate_score
                else CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=gap,
                reason="degraded mode refuses weak lexical auto-select",
                reason_code="DEGRADED_NO_AUTO_SELECT",
                missing_concepts=("product_form",),
            )

        if top.score < self._policy.minimum_auto_select_score:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS
                if top.score >= self._policy.minimum_candidate_score
                else CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=gap,
                reason=(
                    f"top score {top.score:.3f} below minimum_auto_select_score"
                ),
                reason_code="BELOW_AUTO_SELECT_SCORE",
                missing_concepts=("product_form",),
            )

        if gap is not None and gap <= self._policy.minimum_auto_select_gap:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason=(
                    f"score gap {gap:.3f} within minimum_auto_select_gap="
                    f"{self._policy.minimum_auto_select_gap:.3f}"
                ),
                reason_code="TOP_SCORE_GAP_TOO_SMALL",
                missing_concepts=("product_form",),
            )

        return CategoryMatchDecision(
            status=CategoryMatchStatus.MATCHED,
            selected_category_id=top.category_id,
            score_gap=gap,
            reason_code="AUTO_SELECTED",
        )


def _is_strong_exact_alias(candidate: CategoryCandidate) -> bool:
    mode = (candidate.signals.alias_mode or "").upper()
    return mode == "EXACT" and candidate.signals.alias >= 0.9


__all__ = ["DecisionPolicy"]
