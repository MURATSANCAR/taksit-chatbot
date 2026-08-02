"""Prod catalog hydration for search CatalogHints (no demo electronics lock-in)."""

from __future__ import annotations

import pytest

from taksitlio.category.matcher import Category, InMemoryCategoryRepository
from taksitlio.product.catalog import InMemoryProductCatalogRepository
from taksitlio.search_sessions.catalog_pool import (
    apply_catalog_hints,
    refresh_orchestrator_from_catalog,
)
from taksitlio.search_sessions.orchestrator import build_empty_orchestrator
from taksitlio.query_understanding import detect_gaps, fast_parse


def test_apply_catalog_hints_recognizes_buzdolabi() -> None:
    orch = build_empty_orchestrator()
    apply_catalog_hints(
        orch,
        categories=[
            Category(
                id=4,
                category_code="HOME_APPLIANCE",
                display_name="Beyaz Eşya",
                description="Ev aletleri",
                synonyms=("beyaz eşya", "buzdolabı", "çamaşır makinesi"),
            )
        ],
    )
    parse = fast_parse("Buzdolabı bakıyorum, 25-30 bin", catalog=orch.catalog)
    assert any(c.resolved_id == "HOME_APPLIANCE" for c in parse.positive_categories)
    gaps = detect_gaps(parse, category_candidates=orch.category_clarify_options)
    assert gaps.confidence_band in {"HIGH", "MEDIUM"}
    assert not any(u.field == "product_type" for u in gaps.uncertainties)


@pytest.mark.asyncio
async def test_refresh_loads_categories_from_repo() -> None:
    orch = build_empty_orchestrator()
    # Start from empty — must not retain synthetic demo categories.
    assert orch.catalog.categories == ()
    repo = InMemoryCategoryRepository(
        [
            Category(
                id=1,
                category_code="HOME_APPLIANCE",
                display_name="Beyaz Eşya",
                description="Ev aletleri",
                synonyms=("buzdolabı",),
            ),
            Category(
                id=2,
                category_code="LAPTOP",
                display_name="Dizüstü Bilgisayar",
                description="Laptop",
                synonyms=("laptop",),
            ),
        ]
    )
    catalog = InMemoryProductCatalogRepository()
    n = await refresh_orchestrator_from_catalog(
        orch,
        catalog=catalog,
        categories=repo,
        utterance="buzdolabı bakıyorum",
    )
    assert n == 0  # empty product catalog
    assert orch.product_pool == []
    ids = {c.entity_id for c in orch.catalog.categories}
    assert {"HOME_APPLIANCE", "LAPTOP"}.issubset(ids)
    parse = fast_parse("Buzdolabı bakıyorum", catalog=orch.catalog)
    assert parse.positive_categories[0].resolved_id == "HOME_APPLIANCE"
    assert orch.category_token_map["HOME_APPLIANCE"]
