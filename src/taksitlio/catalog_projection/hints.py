"""Load CatalogHints from entity_search_index (no static typo maps)."""

from __future__ import annotations

from typing import Any, Optional

from taksitlio.catalog_projection.rebuild import CatalogProjectionRepository
from taksitlio.query_understanding.fast_parser import CatalogHints


async def catalog_hints_from_entity_index(
    pool: Any,
    *,
    merchant_limit: int = 500,
    brand_limit: int = 2000,
    category_limit: int = 500,
    institution_limit: int = 200,
) -> CatalogHints:
    repo = CatalogProjectionRepository(pool)
    merchants = await repo.load_entity_candidates("MERCHANT", limit=merchant_limit)
    brands = await repo.load_entity_candidates("BRAND", limit=brand_limit)
    categories = await repo.load_entity_candidates("CATEGORY", limit=category_limit)
    institutions = await repo.load_entity_candidates(
        "FINANCIAL_INSTITUTION", limit=institution_limit
    )
    return CatalogHints(
        merchants=merchants,
        brands=brands,
        categories=categories,
        institutions=institutions,
    )


async def maybe_catalog_hints_from_entity_index(
    pool: Optional[Any],
) -> Optional[CatalogHints]:
    if pool is None:
        return None
    try:
        hints = await catalog_hints_from_entity_index(pool)
    except Exception:  # noqa: BLE001 — projection may be absent pre-migrate
        return None
    if not (hints.merchants or hints.categories or hints.brands):
        return None
    return hints
