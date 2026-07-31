"""ADR-010 P4 — fuzzy entity resolution from catalog (no static maps)."""

from __future__ import annotations

from taksitlio.entity_resolution import (
    EntityCandidate,
    ResolutionAction,
    ResolutionPolicy,
    resolve_entity,
)


def _merchant_catalog() -> tuple[EntityCandidate, ...]:
    # Catalog fixtures for unit tests only — not production typo maps.
    return (
        EntityCandidate(
            entity_id="m-tek",
            display_name="Example Merchant A",
            canonical_name="Example Merchant A",
            aliases=("exmple merchant a", "örnek mağaza a"),
            entity_type="merchant",
        ),
        EntityCandidate(
            entity_id="m-med",
            display_name="Example Merchant B",
            canonical_name="Example Merchant B",
            aliases=("exmple merchant b",),
            entity_type="merchant",
        ),
    )


def test_exact_canonical_auto_selects() -> None:
    result = resolve_entity("Example Merchant A", _merchant_catalog())
    assert result.action is ResolutionAction.AUTO_SELECT
    assert result.resolved_entity_id == "m-tek"


def test_typo_alias_resolves_from_catalog_not_code_map() -> None:
    # Typo is present only as catalog alias data, never as if query==...
    result = resolve_entity("exmple merchant a", _merchant_catalog())
    assert result.resolved_entity_id == "m-tek" or result.action in {
        ResolutionAction.AUTO_SELECT,
        ResolutionAction.CLARIFY,
    }
    assert result.candidates[0].entity_id == "m-tek"


def test_ambiguous_close_scores_clarify() -> None:
    catalog = (
        EntityCandidate("1", "Alpha Store", "Alpha Store", aliases=("alpha",)),
        EntityCandidate("2", "Alpha Shop", "Alpha Shop", aliases=("alphaa",)),
    )
    result = resolve_entity(
        "alpha",
        catalog,
        policy=ResolutionPolicy(auto_select_min=0.99, clarify_min=0.50, min_candidate_gap=0.20),
    )
    assert result.action in {ResolutionAction.CLARIFY, ResolutionAction.MULTI_OR_ROUTE}
    assert result.resolved_entity_id is None


def test_low_confidence_routes() -> None:
    result = resolve_entity(
        "zzzz",
        _merchant_catalog(),
        policy=ResolutionPolicy(auto_select_min=0.92, clarify_min=0.78),
    )
    assert result.action in {
        ResolutionAction.MULTI_OR_ROUTE,
        ResolutionAction.UNRESOLVED,
        ResolutionAction.CLARIFY,
    }
