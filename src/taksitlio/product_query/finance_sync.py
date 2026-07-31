"""Persist rebuilt finance projection rows into the finance option index."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from taksitlio.campaign_catalog.feed_apply import InMemoryCampaignCatalog
from taksitlio.campaign_catalog.term_options import build_term_options
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


def term_options_from_memory_catalog(
    catalog: InMemoryCampaignCatalog,
    *,
    merchant_code: str,
    institution_ids: Optional[dict[str, str]] = None,
) -> tuple[InstitutionTermOption, ...]:
    """Build term options from an in-memory catalog for one merchant."""

    id_map = dict(institution_ids or {})
    for code in catalog.institutions:
        id_map.setdefault(code, code)
    return build_term_options(
        campaigns=tuple(catalog.campaigns_by_code.values()),
        rates=tuple(catalog.rates),
        merchant_code=merchant_code,
        institution_ids=id_map,
        require_active=True,
        require_agreement=True,
    )


async def sync_finance_from_memory_catalog(
    index: FinanceOptionIndex,
    catalog: InMemoryCampaignCatalog,
    *,
    product_id: str,
    offer: OfferFinanceContext,
) -> tuple[ProductFinanceOptionRow, ...]:
    """Catalog → term options → payment plan projection → index (best_finance ready)."""

    options = term_options_from_memory_catalog(
        catalog, merchant_code=offer.merchant_code
    )
    return await sync_finance_options_for_product(
        index,
        product_id=product_id,
        offer=offer,
        term_options=options,
    )


async def sync_finance_from_postgres(
    index: FinanceOptionIndex,
    conn: Any,
    *,
    product_id: str,
    offer: OfferFinanceContext,
) -> tuple[ProductFinanceOptionRow, ...]:
    """Load ACTIVE campaigns/rates for merchant from DB, rebuild projection."""

    from taksitlio.campaign_catalog.postgres import load_term_option_inputs_for_merchant

    campaigns, rates, institution_ids = await load_term_option_inputs_for_merchant(
        conn, merchant_code=offer.merchant_code
    )
    options = build_term_options(
        campaigns=campaigns,
        rates=rates,
        merchant_code=offer.merchant_code,
        institution_ids=institution_ids,
        require_active=True,
        require_agreement=True,
    )
    return await sync_finance_options_for_product(
        index,
        product_id=product_id,
        offer=offer,
        term_options=options,
    )


__all__ = [
    "sync_finance_from_memory_catalog",
    "sync_finance_from_postgres",
    "sync_finance_options_for_product",
    "term_options_from_memory_catalog",
]
