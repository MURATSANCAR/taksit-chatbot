"""ADR-013: data-driven fuzzy merchant resolution without static typo maps.

A merchant exists only in an in-memory catalog (not as a query→entity map in
production source). A typo query must resolve after catalog insert — no deploy
of hardcoded aliases in code.
"""

from __future__ import annotations

from taksitlio.entity_resolution import EntityCandidate, resolve_entity
from taksitlio.query_understanding import CatalogHints, fast_parse


def test_new_merchant_typo_resolves_from_catalog_only() -> None:
    # Merchant not present in shipped demo/golden fixtures — invented for this test.
    catalog = CatalogHints(
        merchants=(
            EntityCandidate(
                entity_id="merchant-nova-shop-test",
                display_name="NovaShop",
                canonical_name="NovaShop",
                # Alias is catalog *data* (as admin would store), not a static map in code.
                aliases=("novashoop", "nova shop"),
                entity_type="merchant",
            ),
        ),
        categories=(
            EntityCandidate(
                entity_id="category-laptop",
                display_name="Dizüstü Bilgisayar",
                canonical_name="Laptop",
                aliases=("laptop", "notebook"),
                entity_type="category",
            ),
        ),
    )

    # Typo form never appears as a production static mapping target.
    message = "Novashoop’tan laptop istiyorum"
    parsed = fast_parse(message, catalog=catalog)
    assert parsed.merchant is not None
    assert parsed.merchant.resolved_id == "merchant-nova-shop-test"
    assert parsed.merchant.display_name == "NovaShop"


def test_resolve_entity_fuzzy_without_code_map() -> None:
    candidates = (
        EntityCandidate(
            entity_id="merchant-zenmart-test",
            display_name="ZenMart",
            canonical_name="ZenMart",
            aliases=("zen maart",),
            entity_type="merchant",
        ),
    )
    result = resolve_entity("zen maart", candidates)
    assert result.resolved_entity_id == "merchant-zenmart-test"
    assert result.resolved_display_name == "ZenMart"


def test_empty_catalog_does_not_invent_merchant() -> None:
    parsed = fast_parse("Novashoop’tan laptop", catalog=CatalogHints())
    assert parsed.merchant is None or parsed.merchant.resolved_id is None
