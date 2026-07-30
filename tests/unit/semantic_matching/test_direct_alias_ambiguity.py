"""Direct alias auto-select vs sibling ambiguity (ADR-006 §F)."""

from __future__ import annotations

from taksitlio.semantic_matching.decision_policy import DecisionPolicy
from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    CategoryMatchStatus,
    SemanticMatchPolicy,
    SignalBreakdown,
)


def _cand(
    cid: str,
    score: float,
    rank: int,
    *,
    direct: bool = False,
    alias_mode: str | None = None,
    alias_score: float = 0.0,
) -> CategoryCandidate:
    signals = SignalBreakdown(
        alias=alias_score,
        alias_mode=alias_mode,
        direct_alias_match=direct,
    )
    return CategoryCandidate(
        category_id=cid,
        slug=cid,
        display_name=cid,
        score=score,
        rank=rank,
        signals=signals,
    )


def test_single_direct_alias_auto_selects_even_with_tight_gap() -> None:
    policy = SemanticMatchPolicy(
        minimum_candidate_score=0.3,
        minimum_auto_select_score=0.5,
        minimum_auto_select_gap=0.15,
        direct_alias_can_reduce_ambiguity=True,
    )
    top = _cand("a", 0.55, 1, direct=True, alias_mode="EXACT", alias_score=0.95)
    second = _cand("b", 0.50, 2)
    verdict = DecisionPolicy(policy).decide((top, second))
    assert verdict.status is CategoryMatchStatus.MATCHED
    assert verdict.selected_category_id == "a"
    assert verdict.reason_code == "DIRECT_ALIAS_AUTO_SELECT"


def test_two_direct_aliases_at_top_force_clarification() -> None:
    policy = SemanticMatchPolicy(
        minimum_candidate_score=0.3,
        minimum_auto_select_score=0.5,
        direct_alias_can_reduce_ambiguity=True,
        direct_alias_conflict_requires_clarification=True,
    )
    top = _cand("a", 0.7, 1, direct=True, alias_mode="EXACT", alias_score=0.95)
    other = _cand("b", 0.68, 2, direct=True, alias_mode="EXACT", alias_score=0.95)
    verdict = DecisionPolicy(policy).decide((top, other))
    assert verdict.status is CategoryMatchStatus.AMBIGUOUS
    assert verdict.reason_code == "DIRECT_ALIAS_CONFLICT"


def test_tight_sibling_gap_without_direct_alias_is_sibling_ambiguity() -> None:
    policy = SemanticMatchPolicy(
        minimum_candidate_score=0.3,
        minimum_auto_select_score=0.4,
        minimum_auto_select_gap=0.10,
        direct_alias_can_reduce_ambiguity=True,
    )
    top = _cand("a", 0.55, 1)
    other = _cand("b", 0.50, 2)
    verdict = DecisionPolicy(policy).decide((top, other))
    assert verdict.status is CategoryMatchStatus.AMBIGUOUS
    assert verdict.reason_code == "SIBLING_AMBIGUITY"


def test_wide_gap_auto_selects_without_direct_alias() -> None:
    policy = SemanticMatchPolicy(
        minimum_candidate_score=0.2,
        minimum_auto_select_score=0.4,
        minimum_auto_select_gap=0.05,
    )
    top = _cand("a", 0.85, 1)
    other = _cand("b", 0.4, 2)
    verdict = DecisionPolicy(policy).decide((top, other))
    assert verdict.status is CategoryMatchStatus.MATCHED
    assert verdict.reason_code == "AUTO_SELECTED"
