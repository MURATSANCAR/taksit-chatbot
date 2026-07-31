"""Diversify Top-K unit tests (ADR-008 P0.1).

Focus areas:

* ``diversify_top_k`` never lifts a hard-excluded / non-matchable node.
* Mild parent demotion runs when a viable child is present.
* Signal-preference slot fill promotes a lower-ranked candidate that
  actually has a positive channel over a pure-vector fallback.
* Sibling diversity avoids stacking two siblings in Top-2 when a
  candidate from a different parent has a positive channel.
"""

from __future__ import annotations

from taksitlio.category_catalog.domain import CategorySnapshot, CategorySnapshotNode
from taksitlio.semantic_matching.diversification import diversify_top_k
from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    SemanticMatchPolicy,
    SignalBreakdown,
)
from taksitlio.semantic_matching.in_memory_index import SnapshotIndex


def _node(id_: str, *, parent: str | None = None, ancestors: tuple[str, ...] = ()) -> CategorySnapshotNode:
    return CategorySnapshotNode(
        id=id_,
        catalog_id="cat",
        slug=id_,
        parent_id=parent,
        depth=len(ancestors),
        display_name=id_,
        description="",
        semantic_description="",
        synonyms=(),
        aliases=(),
        use_cases=(),
        locale="tr-TR",
        ancestor_ids=ancestors,
    )


def _snapshot(*nodes: CategorySnapshotNode) -> SnapshotIndex:
    snap = CategorySnapshot(
        catalog_id="cat",
        catalog_code="CAT",
        revision=1,
        primary_locale="tr-TR",
        locale="tr-TR",
        match_policy_code="P",
        nodes=nodes,
    )
    return SnapshotIndex.build(snap)


def _cand(
    id_: str,
    score: float,
    rank: int,
    *,
    signals: SignalBreakdown | None = None,
) -> CategoryCandidate:
    return CategoryCandidate(
        category_id=id_,
        slug=id_,
        display_name=id_,
        score=score,
        rank=rank,
        signals=signals or SignalBreakdown(),
    )


def test_empty_input_returns_empty_outcome() -> None:
    idx = _snapshot(_node("a"))
    policy = SemanticMatchPolicy()
    outcome = diversify_top_k([], index=idx, policy=policy)
    assert outcome.candidates == ()
    assert outcome.diversity_notes == ()


def test_parent_with_viable_child_is_demoted() -> None:
    """Parent at rank 1 loses score by ``same_parent_penalty`` when a
    scored child is also present. Child can then take Top-1."""

    idx = _snapshot(
        _node("parent"),
        _node("child", parent="parent", ancestors=("parent",)),
    )
    policy = SemanticMatchPolicy(
        diversification_enabled=True,
        same_parent_penalty=0.10,
        minimum_candidate_score=0.20,
        maximum_candidates=3,
    )
    candidates = (
        _cand("parent", 0.60, 1),
        _cand(
            "child",
            0.55,
            2,
            signals=SignalBreakdown(alias=0.6, direct_alias_match=True),
        ),
    )
    outcome = diversify_top_k(candidates, index=idx, policy=policy)
    ids = [c.category_id for c in outcome.candidates]
    # After demotion (parent 0.60 -> 0.50), child (0.55) is Top-1.
    assert ids[0] == "child"
    assert "parent_demoted:parent" in " ".join(outcome.diversity_notes)


def test_signal_preference_promotes_positive_channel_over_vector() -> None:
    idx = _snapshot(_node("a"), _node("b"), _node("c"))
    policy = SemanticMatchPolicy(
        diversification_enabled=True,
        prefer_positive_channel_in_topk=True,
        sibling_diversity_enabled=False,
        maximum_candidates=2,
        minimum_candidate_score=0.10,
    )
    candidates = (
        _cand("a", 0.80, 1, signals=SignalBreakdown(alias=0.9)),
        _cand("b", 0.55, 2, signals=SignalBreakdown(vector=0.55)),
        _cand("c", 0.52, 3, signals=SignalBreakdown(alias=0.5)),
    )
    outcome = diversify_top_k(candidates, index=idx, policy=policy)
    ids = [c.category_id for c in outcome.candidates]
    # ``c`` (positive channel) is preferred over ``b`` (vector only) for rank 2.
    assert ids == ["a", "c"]
    assert any(note.startswith("signal_prefer:") for note in outcome.diversity_notes)


def test_sibling_diversity_prefers_other_parent_in_top_2() -> None:
    idx = _snapshot(
        _node("p1"),
        _node("p2"),
        _node("p1_a", parent="p1", ancestors=("p1",)),
        _node("p1_b", parent="p1", ancestors=("p1",)),
        _node("p2_x", parent="p2", ancestors=("p2",)),
    )
    policy = SemanticMatchPolicy(
        diversification_enabled=True,
        prefer_positive_channel_in_topk=True,
        sibling_diversity_enabled=True,
        maximum_candidates=2,
        minimum_candidate_score=0.10,
    )
    candidates = (
        _cand("p1_a", 0.70, 1, signals=SignalBreakdown(alias=0.7)),
        _cand("p1_b", 0.55, 2, signals=SignalBreakdown(alias=0.5)),
        _cand("p2_x", 0.50, 3, signals=SignalBreakdown(alias=0.4)),
    )
    outcome = diversify_top_k(candidates, index=idx, policy=policy)
    ids = [c.category_id for c in outcome.candidates]
    assert ids[0] == "p1_a"
    assert ids[1] == "p2_x"
    assert any(note.startswith("sibling_diverse:") for note in outcome.diversity_notes)


def test_never_reintroduces_dropped_candidates() -> None:
    idx = _snapshot(_node("a"), _node("b"))
    policy = SemanticMatchPolicy(
        diversification_enabled=True,
        maximum_candidates=1,
        minimum_candidate_score=0.10,
    )
    candidates = (
        _cand("a", 0.80, 1, signals=SignalBreakdown(alias=0.9)),
        _cand("b", 0.60, 2, signals=SignalBreakdown(alias=0.5)),
    )
    outcome = diversify_top_k(candidates, index=idx, policy=policy)
    assert [c.category_id for c in outcome.candidates] == ["a"]


def test_disabled_diversification_preserves_input_order() -> None:
    idx = _snapshot(
        _node("parent"),
        _node("child", parent="parent", ancestors=("parent",)),
    )
    policy = SemanticMatchPolicy(
        diversification_enabled=False,
        maximum_candidates=3,
        minimum_candidate_score=0.10,
    )
    candidates = (
        _cand("parent", 0.60, 1, signals=SignalBreakdown(direct_alias_match=True)),
        _cand("child", 0.55, 2, signals=SignalBreakdown(alias=0.7)),
    )
    outcome = diversify_top_k(candidates, index=idx, policy=policy)
    ids = [c.category_id for c in outcome.candidates]
    assert ids == ["parent", "child"]
    assert not any(n.startswith("parent_demoted:") for n in outcome.diversity_notes)
