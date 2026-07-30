"""ADR-008 P0 — TokenSetAliasRetriever behavioural tests.

Guarantees under test:

* Same token *set* in a different order still exact-token-set matches.
* Single-token query does NOT satisfy the exact-set channel against a
  multi-token alias.
* ``masa`` MUST NOT substring-match ``masaüstü`` — no channel fires.
* A character n-gram hit alone must not set ``surface_exact`` / does not
  make the retriever's ``is_surface_exact`` flip.
* A surface / normalized / token-set negative concept hard-excludes.
* A character n-gram-only match does NOT hard-exclude.
"""

from __future__ import annotations

from taksitlio.category_catalog.domain import Alias, CategorySnapshotNode, MatchMode
from taksitlio.semantic_matching.token_set_alias_retriever import (
    AliasSignalKind,
    TokenSetAliasRetriever,
)


def _make_node(
    display_name: str,
    *,
    aliases: tuple[str, ...] = (),
    synonyms: tuple[str, ...] = (),
    node_id: str = "cat",
) -> CategorySnapshotNode:
    alias_objects = tuple(
        Alias(
            id=f"a-{i}",
            category_id=node_id,
            locale="tr-TR",
            alias_text=text,
            alias_type=MatchMode.EXACT,
            weight=1.0,
        )
        for i, text in enumerate(aliases)
    )
    return CategorySnapshotNode(
        id=node_id,
        catalog_id="cat-1",
        slug=display_name.lower().replace(" ", "-"),
        parent_id=None,
        depth=0,
        display_name=display_name,
        description="",
        semantic_description="",
        synonyms=synonyms,
        aliases=alias_objects,
        use_cases=(),
        locale="tr-TR",
        ancestor_ids=(),
    )


def test_same_token_set_reordered_matches_exact_token_set() -> None:
    """``sistemi ses`` and ``ses sistemi`` share the same token set."""

    retriever = TokenSetAliasRetriever()
    node = _make_node("Ses Sistemi", aliases=("ses sistemi",))
    score = retriever.score(("sistemi ses",), node)
    assert score.token_set >= 0.9
    assert score.surface_exact == 0.0
    assert score.character_ngram <= score.token_set


def test_single_token_query_does_not_exact_match_multi_token_alias() -> None:
    """Single-token ``bilgisayar`` must not exact-set match ``masaüstü bilgisayar``."""

    retriever = TokenSetAliasRetriever()
    node = _make_node(
        "Masaüstü Bilgisayar", aliases=("masaüstü bilgisayar",)
    )
    score = retriever.score(("bilgisayar",), node)
    # Single-token whole-token membership channel is allowed and marked
    # SURFACE_EXACT_PHRASE (see AliasSignalKind), but the exact-token-set
    # channel must not fire since |q_set| != |a_set|.
    assert score.token_set == 0.0


def test_masa_does_not_substring_match_masaustu() -> None:
    """Bare substring ``masa`` in ``masaüstü`` must not fire any channel."""

    retriever = TokenSetAliasRetriever()
    node = _make_node("Masaüstü Bilgisayar", aliases=("masaüstü bilgisayar",))
    score = retriever.score(("masa",), node)
    assert score.surface_exact == 0.0
    assert score.normalized_exact == 0.0
    assert score.token_set == 0.0
    assert score.prefix_safe == 0.0
    # ngram similarity between ``masa`` and ``masaüstü bilgisayar`` is
    # well below the 0.78 default — no fuzzy hit either.
    assert score.character_ngram == 0.0
    assert score.aggregate_alias == 0.0
    assert not retriever.matches_negative_hard_exclude(("masa",), node)


def test_character_ngram_alone_does_not_set_is_surface_exact() -> None:
    """A pure n-gram fuzzy hit must never flip the ``is_surface_exact`` bit."""

    # Lower the ngram threshold so a controlled typo-shaped input fires.
    retriever = TokenSetAliasRetriever(character_ngram_min_similarity=0.5)
    node = _make_node("Bilgisayar", aliases=("bilgisayar",))
    score = retriever.score(("bilgsayar",), node)  # missing "i"
    assert score.character_ngram > 0
    assert score.surface_exact == 0.0
    assert score.normalized_exact == 0.0
    assert not score.is_surface_exact
    assert not score.is_strong_exact
    # ADR-008: the "best" hit for a pure n-gram must be labelled as such,
    # never as SURFACE_EXACT_PHRASE.
    assert score.best_hit is not None
    assert score.best_hit.kind is AliasSignalKind.CHARACTER_NGRAM


def test_negative_surface_hard_excludes_matching_alias() -> None:
    """Explicit surface form on the negative side triggers hard-exclude."""

    retriever = TokenSetAliasRetriever()
    mobile = _make_node("Mobil", aliases=("telefon", "cep telefonu"))
    assert retriever.matches_negative_hard_exclude(("telefon",), mobile)


def test_negative_character_ngram_alone_does_not_hard_exclude() -> None:
    """A fuzzy n-gram hit on the negative side must NOT hard-exclude.

    Regression against pre-ADR-008 behaviour where ``trigram_similarity
    >= 0.85`` was enough to remove a node.
    """

    retriever = TokenSetAliasRetriever(character_ngram_min_similarity=0.5)
    node = _make_node("Bilgisayar", aliases=("bilgisayar",))
    # Typo close enough that character_ngram fires above the (lowered)
    # threshold, but not exact / not token-set.
    assert not retriever.matches_negative_hard_exclude(("bilgsayar",), node)


def test_negative_token_set_still_hard_excludes() -> None:
    """Same token-set reordered still counts as a hard-exclude on negative."""

    retriever = TokenSetAliasRetriever()
    node = _make_node("Ses Sistemi", aliases=("ses sistemi",))
    assert retriever.matches_negative_hard_exclude(("sistemi ses",), node)
