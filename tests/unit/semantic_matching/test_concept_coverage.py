"""Concept coverage scorer unit tests (ADR-008 P0.1)."""

from __future__ import annotations

from taksitlio.category_catalog.domain import (
    Alias,
    CategorySnapshotNode,
    MatchMode,
    UseCase,
)
from taksitlio.semantic_matching.concept_coverage import ConceptCoverageScorer


def _alias(text: str, cid: str = "n") -> Alias:
    return Alias(
        id=f"a-{cid}-{text}",
        category_id=cid,
        locale="tr-TR",
        alias_text=text,
        alias_type=MatchMode.EXACT,
        weight=1.0,
    )


def _use_case(text: str, cid: str = "n") -> UseCase:
    return UseCase(
        id=f"u-{cid}-{text}",
        category_id=cid,
        locale="tr-TR",
        use_case_text=text,
    )


def _node(
    *,
    id_: str = "n",
    aliases: tuple[str, ...] = (),
    synonyms: tuple[str, ...] = (),
    display_name: str = "N",
    semantic_description: str = "",
    use_cases: tuple[str, ...] = (),
) -> CategorySnapshotNode:
    return CategorySnapshotNode(
        id=id_,
        catalog_id="cat",
        slug=id_,
        parent_id=None,
        depth=0,
        display_name=display_name,
        description="",
        semantic_description=semantic_description,
        synonyms=synonyms,
        aliases=tuple(_alias(a, id_) for a in aliases),
        use_cases=tuple(_use_case(u, id_) for u in use_cases),
        locale="tr-TR",
        ancestor_ids=(),
    )


def test_empty_positive_returns_zero_bonus() -> None:
    scorer = ConceptCoverageScorer(coverage_weight=0.10)
    node = _node(aliases=("koltuk",))
    score = scorer.score([], node)
    assert score.matched_positive_concept_count == 0
    assert scorer.bonus(score) == 0.0


def test_alias_hit_counts_as_full_cover() -> None:
    scorer = ConceptCoverageScorer(coverage_weight=0.10)
    node = _node(aliases=("koltuk", "yatak"))
    score = scorer.score(["koltuk"], node)
    assert score.matched_positive_concept_count == 1
    assert score.weighted_concept_coverage == 1.0
    assert scorer.bonus(score) > 0.0


def test_context_text_hit_yields_soft_cover() -> None:
    scorer = ConceptCoverageScorer(coverage_weight=0.10)
    # "sandalye" only appears in semantic_description — no alias hit.
    node = _node(
        aliases=("koltuk", "yatak"),
        semantic_description="Mobilya ihtiyacı; kanepe, yatak, masa, sandalye.",
    )
    score = scorer.score(["sandalye"], node)
    assert score.matched_positive_concept_count == 1
    assert 0.0 < score.weighted_concept_coverage < 1.0
    assert scorer.bonus(score) > 0.0


def test_excluded_concepts_never_contribute() -> None:
    scorer = ConceptCoverageScorer(coverage_weight=0.10)
    node = _node(aliases=("saat",))
    score = scorer.score(["saat"], node, excluded_concepts=["saat"])
    assert score.matched_positive_concept_count == 0
    assert scorer.bonus(score) == 0.0


def test_multi_concept_coverage_gets_extra_bump() -> None:
    scorer = ConceptCoverageScorer(coverage_weight=0.10)
    node = _node(aliases=("koltuk", "yatak"))
    single = scorer.score(["koltuk"], node)
    double = scorer.score(["koltuk", "yatak"], node)
    assert double.matched_positive_concept_count == 2
    assert scorer.bonus(double) > scorer.bonus(single)


def test_bonus_is_capped() -> None:
    # A large coverage_weight with many matches must still be capped.
    scorer = ConceptCoverageScorer(coverage_weight=1.0)
    node = _node(aliases=("a", "b", "c", "d"))
    score = scorer.score(["a", "b", "c", "d"], node)
    assert scorer.bonus(score) <= 0.20 + 1e-9


def test_use_case_text_hit_yields_soft_cover() -> None:
    scorer = ConceptCoverageScorer(coverage_weight=0.10)
    node = _node(
        aliases=("mobilya",),
        use_cases=("Yeni evime yatak odası takımı",),
    )
    # "yatak" appears only in the use-case text — alias miss, context hit.
    score = scorer.score(["yatak"], node)
    assert score.matched_positive_concept_count == 1
    assert scorer.bonus(score) > 0.0
