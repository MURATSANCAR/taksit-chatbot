"""Decision policy that converts a candidate list into a typed match status.

Order (ADR-006 F):

    1. Dependency guard    — empty pool → NO_MATCH
    2. Hard negatives      — matcher already stripped exact-negative /
                             correction hits; guard preserved for safety.
    3. Minimum score       — top candidate must clear minimum_candidate_score.
    4. Direct alias        — EXACT + high weight can auto-select.
    5. Parent-child        — collapsed pair produced by hierarchy helper.
    6. Auto-select gap     — top score above threshold and gap wide enough.
    7. Direct-alias vs alias — two direct aliases in top-2 → clarification.
    8. Ambiguous / no-match — fallback.

Reason codes:
    NO_ELIGIBLE_CANDIDATES, BELOW_MINIMUM_CANDIDATE_SCORE,
    BELOW_AUTO_SELECT_SCORE, TOP_SCORE_GAP_TOO_SMALL, PARENT_CHILD_COLLAPSED,
    PARENT_CHILD_REQUIRES_CLARIFICATION, SIBLING_AMBIGUITY,
    DIRECT_ALIAS_AUTO_SELECT, DEGRADED_EXACT_ALIAS, DEGRADED_NO_AUTO_SELECT,
    AUTO_SELECTED.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    CategoryMatchDecision,
    CategoryMatchStatus,
    SemanticMatchPolicy,
)
from taksitlio.semantic_matching.query_intent import QueryIntentKind


class DecisionPolicy:
    def __init__(self, policy: SemanticMatchPolicy) -> None:
        self._policy = policy

    def decide(
        self,
        candidates: Sequence[CategoryCandidate],
        *,
        degraded: bool = False,
        collapsed_pairs: Sequence[tuple[str, str]] = (),
        multi_need_signal: bool = False,
        intent_kind: Optional[QueryIntentKind] = None,
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
        gap: Optional[float] = None
        if len(eligible) >= 2:
            gap = top.score - eligible[1].score

        # Out-of-scope / non-matchable nodes may retrieve but never MATCHED.
        if not top.matchable:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=gap,
                reason="top candidate is non-matchable / out-of-scope",
                reason_code="OUT_OF_SCOPE_TOP_CANDIDATE",
            )

        if degraded:
            return self._decide_degraded(eligible, top, gap)

        # ADR-007 §9: non-purchase / OOS / choice never auto-select — even
        # when a product alias string appears in the utterance.
        if intent_kind is QueryIntentKind.OUT_OF_SCOPE:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=gap,
                reason="out-of-scope intent refuses category auto-select",
                reason_code="OUT_OF_SCOPE_INTENT",
            )
        if intent_kind is QueryIntentKind.NON_PURCHASE:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=gap,
                reason="non-purchase intent refuses category auto-select",
                reason_code="NON_PURCHASE_INTENT",
            )
        if intent_kind is QueryIntentKind.CHOICE:
            # Choice questions ("X mi Y mi") always clarify — do not let a
            # single direct alias collapse the sibling pair.
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason="choice / multi-need utterance requires clarification",
                reason_code="MULTI_NEED_FALSE_MATCH_GUARD",
                missing_concepts=("need_prioritisation",),
            )

        # ADR-007 §G: when the caller explicitly signals "user said two
        # distinct needs at once" (multi_need_signal) and both are still
        # in the pool, prefer AMBIGUOUS over auto-select.
        if (
            multi_need_signal
            and len(eligible) >= 2
            and gap is not None
            and gap <= self._policy.multi_need_ambiguity_gap
        ):
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason="multi-need signal keeps top-2 ambiguous",
                reason_code="MULTI_NEED_SIGNAL_AMBIGUOUS",
                missing_concepts=("need_prioritisation",),
            )

        # Direct alias auto-select — reduces false ambiguity on clear intents.
        if self._policy.direct_alias_can_reduce_ambiguity:
            direct_verdict = self._direct_alias_verdict(eligible, top, gap)
            if direct_verdict is not None:
                return direct_verdict

        # ADR-007 §G: weak-lexical-only top candidate never auto-selects.
        # If the top score comes purely from lexical / trigram fuzz and
        # neither alias nor vector nor use-case fired, we require the
        # gap to be substantial before auto-selecting; otherwise fall
        # through to the standard gap check (which becomes AMBIGUOUS).
        if _is_weak_lexical_only(top) and top.score < (
            self._policy.minimum_auto_select_score
            + self._policy.weak_lexical_extra_headroom
        ):
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason="weak lexical-only top candidate — refuse auto-select",
                reason_code="WEAK_LEXICAL_ONLY_TOP",
                missing_concepts=("product_form",),
            )

        # Parent-child collapse verdict: if hierarchy helper collapsed a
        # pair and the survivor is the top, note it in the reason code so
        # dashboards can distinguish this from a plain auto-select.
        collapsed = bool(collapsed_pairs)
        if collapsed and top.signals.hierarchy_collapsed:
            if top.score >= self._policy.minimum_auto_select_score and (
                gap is None or gap > self._policy.minimum_auto_select_gap
            ):
                return CategoryMatchDecision(
                    status=CategoryMatchStatus.MATCHED,
                    selected_category_id=top.category_id,
                    score_gap=gap,
                    reason="parent-child pair collapsed to specific",
                    reason_code="PARENT_CHILD_COLLAPSED",
                )
            # Collapsed but still ambiguous → clarify against the survivor.
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason="parent-child pair requires clarification",
                reason_code="PARENT_CHILD_REQUIRES_CLARIFICATION",
                missing_concepts=("product_specificity",),
            )

        if top.score < self._policy.minimum_auto_select_score:
            status = (
                CategoryMatchStatus.AMBIGUOUS
                if top.score >= self._policy.minimum_candidate_score
                else CategoryMatchStatus.NO_MATCH
            )
            return CategoryMatchDecision(
                status=status,
                selected_category_id=None,
                score_gap=gap,
                reason=(
                    f"top score {top.score:.3f} below minimum_auto_select_score"
                ),
                reason_code="BELOW_AUTO_SELECT_SCORE",
                missing_concepts=("product_form",),
            )

        if gap is not None and gap <= self._policy.minimum_auto_select_gap:
            # Distinguish sibling ambiguity for observability.
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason=(
                    f"score gap {gap:.3f} within minimum_auto_select_gap="
                    f"{self._policy.minimum_auto_select_gap:.3f}"
                ),
                reason_code="SIBLING_AMBIGUITY",
                missing_concepts=("product_form",),
            )

        return CategoryMatchDecision(
            status=CategoryMatchStatus.MATCHED,
            selected_category_id=top.category_id,
            score_gap=gap,
            reason_code="AUTO_SELECTED",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _direct_alias_verdict(
        self,
        eligible: Sequence[CategoryCandidate],
        top: CategoryCandidate,
        gap: Optional[float],
    ) -> Optional[CategoryMatchDecision]:
        if not top.signals.direct_alias_match:
            return None

        second_direct = (
            len(eligible) >= 2 and eligible[1].signals.direct_alias_match
        )
        if second_direct and self._policy.direct_alias_conflict_requires_clarification:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.AMBIGUOUS,
                selected_category_id=None,
                score_gap=gap,
                reason=(
                    "multiple direct alias matches at top of ranking"
                ),
                reason_code="DIRECT_ALIAS_CONFLICT",
                missing_concepts=("product_form",),
            )
        # Single direct alias — never auto-select non-matchable / OOS.
        if not top.matchable:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.NO_MATCH,
                selected_category_id=None,
                score_gap=gap,
                reason="direct alias hits non-matchable category",
                reason_code="OUT_OF_SCOPE_TOP_CANDIDATE",
            )
        if top.score >= self._policy.minimum_candidate_score:
            return CategoryMatchDecision(
                status=CategoryMatchStatus.MATCHED,
                selected_category_id=top.category_id,
                score_gap=gap,
                reason="direct alias auto-select",
                reason_code="DIRECT_ALIAS_AUTO_SELECT",
            )
        return None

    def _decide_degraded(
        self,
        eligible: Sequence[CategoryCandidate],
        top: CategoryCandidate,
        gap: Optional[float],
    ) -> CategoryMatchDecision:
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


def _is_strong_exact_alias(candidate: CategoryCandidate) -> bool:
    mode = (candidate.signals.alias_mode or "").upper()
    return mode == "EXACT" and candidate.signals.alias >= 0.9


def _is_weak_lexical_only(candidate: CategoryCandidate) -> bool:
    """True when only the lexical/trigram channel produced the top score.

    Alias, vector and use-case channels are all zero — that's the
    "we matched some characters" fallback signal (ADR-007 §G).
    """

    signals = candidate.signals
    return (
        signals.alias <= 0.0
        and signals.vector <= 0.0
        and signals.use_case <= 0.0
        and signals.lexical > 0.0
    )


__all__ = ["DecisionPolicy"]
