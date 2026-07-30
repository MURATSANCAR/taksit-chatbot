"""Parent-child collapse unit tests (ADR-006 §F)."""

from __future__ import annotations

from taksitlio.category_catalog.domain import CategorySnapshot, CategorySnapshotNode
from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    SemanticMatchPolicy,
    SignalBreakdown,
)
from taksitlio.semantic_matching.hierarchy_collapse import collapse_parent_child
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


def _cand(id_: str, score: float, rank: int) -> CategoryCandidate:
    return CategoryCandidate(
        category_id=id_,
        slug=id_,
        display_name=id_,
        score=score,
        rank=rank,
        signals=SignalBreakdown(),
    )


def test_parent_child_collapses_to_more_specific_child() -> None:
    idx = _snapshot(
        _node("parent"),
        _node("child", parent="parent", ancestors=("parent",)),
    )
    policy = SemanticMatchPolicy(
        parent_child_collapse_enabled=True,
        parent_child_collapse_gap=0.15,
    )
    candidates = (_cand("parent", 0.90, 1), _cand("child", 0.82, 2))
    outcome = collapse_parent_child(candidates, index=idx, policy=policy)
    assert len(outcome.candidates) == 1
    assert outcome.candidates[0].category_id == "child"
    assert outcome.candidates[0].signals.hierarchy_collapsed is True
    assert outcome.collapsed_pairs == (("child", "parent"),)


def test_no_collapse_when_gap_exceeds_threshold() -> None:
    idx = _snapshot(
        _node("parent"),
        _node("child", parent="parent", ancestors=("parent",)),
    )
    policy = SemanticMatchPolicy(
        parent_child_collapse_enabled=True,
        parent_child_collapse_gap=0.05,
    )
    candidates = (_cand("parent", 0.95, 1), _cand("child", 0.60, 2))
    outcome = collapse_parent_child(candidates, index=idx, policy=policy)
    assert len(outcome.candidates) == 2
    assert outcome.collapsed_pairs == ()


def test_siblings_are_never_collapsed() -> None:
    idx = _snapshot(
        _node("parent"),
        _node("child_a", parent="parent", ancestors=("parent",)),
        _node("child_b", parent="parent", ancestors=("parent",)),
    )
    policy = SemanticMatchPolicy(parent_child_collapse_enabled=True)
    candidates = (_cand("child_a", 0.80, 1), _cand("child_b", 0.79, 2))
    outcome = collapse_parent_child(candidates, index=idx, policy=policy)
    assert len(outcome.candidates) == 2
    assert outcome.collapsed_pairs == ()


def test_grandparent_chain_collapses_to_leaf() -> None:
    idx = _snapshot(
        _node("gp"),
        _node("p", parent="gp", ancestors=("gp",)),
        _node("leaf", parent="p", ancestors=("gp", "p")),
    )
    policy = SemanticMatchPolicy(
        parent_child_collapse_enabled=True,
        parent_child_collapse_gap=0.15,
    )
    candidates = (
        _cand("gp", 0.90, 1),
        _cand("p", 0.83, 2),
        _cand("leaf", 0.80, 3),
    )
    outcome = collapse_parent_child(candidates, index=idx, policy=policy)
    assert len(outcome.candidates) == 1
    assert outcome.candidates[0].category_id == "leaf"


def test_disabled_policy_returns_input_unchanged() -> None:
    idx = _snapshot(
        _node("parent"),
        _node("child", parent="parent", ancestors=("parent",)),
    )
    policy = SemanticMatchPolicy(parent_child_collapse_enabled=False)
    candidates = (_cand("parent", 0.90, 1), _cand("child", 0.85, 2))
    outcome = collapse_parent_child(candidates, index=idx, policy=policy)
    assert outcome.candidates == candidates
    assert outcome.collapsed_pairs == ()
