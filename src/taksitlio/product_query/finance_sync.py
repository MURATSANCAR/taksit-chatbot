"""Persist rebuilt finance projection rows into the finance option index."""

from __future__ import annotations

from typing import Optional, Sequence

from taksitlio.product_query.finance_index import FinanceOptionIndex
from taksitlio.product_query.finance_projection import (
    InstitutionTermOption,
    OfferFinanceContext,
    ProductFinanceOptionRow,
    rebuild_finance_options,
)


async def sync_finance_options_for_product(
    index: FinanceOptionIndex,
    *,
    product_id: str,
    offer: OfferFinanceContext,
    term_options: Sequence[InstitutionTermOption],
) -> tuple[ProductFinanceOptionRow, ...]:
    """Rebuild projection and replace index rows for ``product_id``."""

    rows = rebuild_finance_options(offer, term_options)
    await index.put(product_id, rows)
    return rows


__all__ = ["sync_finance_options_for_product"]
