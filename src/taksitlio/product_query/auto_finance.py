"""Automatic finance projection rebuild (ADR-010).

Wires campaign/product ingestion → payment plan → product_finance_options
so chatbot cards get ``best_finance`` without manual admin rebuild.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from taksitlio.campaign_catalog.feed_apply import InMemoryCampaignCatalog
from taksitlio.product_query.finance_index import FinanceOptionIndex
from taksitlio.product_query.finance_projection import OfferFinanceContext
from taksitlio.product_query.finance_sync import (
    sync_finance_from_memory_catalog,
    sync_finance_from_postgres,
)

logger = logging.getLogger("taksitlio.auto_finance")


@dataclass
class FinanceAutoSyncDeps:
    finance_index: FinanceOptionIndex
    product_catalog: Any
    merchant_directory: Any = None
    campaign_catalog: Optional[InMemoryCampaignCatalog] = None
    db_pool: Any = None


@dataclass(frozen=True)
class AutoRebuildStats:
    products_attempted: int
    products_synced: int
    eligible_options: int
    skipped: int
    errors: int


async def _merchant_code_for(
    deps: FinanceAutoSyncDeps, merchant_id: int
) -> Optional[str]:
    directory = deps.merchant_directory
    if directory is None:
        return None
    entry = await directory.get(int(merchant_id))
    if entry is None:
        return None
    return str(entry.merchant_code)


async def _merchant_id_for_code(
    deps: FinanceAutoSyncDeps, merchant_code: str
) -> Optional[int]:
    directory = deps.merchant_directory
    if directory is None:
        return None
    getter = getattr(directory, "get_by_code", None)
    if callable(getter):
        entry = await getter(merchant_code)
        return None if entry is None else int(entry.id)
    list_fn = getattr(directory, "list_active", None)
    if not callable(list_fn):
        return None
    for entry in await list_fn(limit=500):
        if entry.merchant_code == merchant_code:
            return int(entry.id)
    return None


async def rebuild_finance_for_product(
    deps: FinanceAutoSyncDeps,
    *,
    product_id: int,
    merchant_id: int,
    merchant_code: Optional[str] = None,
) -> int:
    """Rebuild projection for one product. Returns eligible option count."""

    offer = await deps.product_catalog.get_offer(int(product_id))
    if offer is None:
        return 0
    code = merchant_code or await _merchant_code_for(deps, merchant_id)
    if not code:
        logger.debug(
            "finance rebuild skip product_id=%s — merchant_code unknown", product_id
        )
        return 0

    ctx = OfferFinanceContext(
        product_offer_id=str(offer.id),
        merchant_id=str(merchant_id),
        merchant_code=code,
        purchase_price=float(offer.price),
        stock_status=str(offer.stock_status or "UNKNOWN"),
        price_freshness=str(offer.freshness_status or "UNVERIFIED"),
    )

    if deps.db_pool is not None:
        async with deps.db_pool.acquire() as conn:
            rows = await sync_finance_from_postgres(
                deps.finance_index,
                conn,
                product_id=str(product_id),
                offer=ctx,
            )
    elif deps.campaign_catalog is not None:
        rows = await sync_finance_from_memory_catalog(
            deps.finance_index,
            deps.campaign_catalog,
            product_id=str(product_id),
            offer=ctx,
        )
    else:
        logger.debug("finance rebuild skip — no campaign catalog or db pool")
        return 0

    eligible = sum(1 for r in rows if r.eligibility_status == "ELIGIBLE")
    logger.info(
        "finance rebuilt product_id=%s options=%s eligible=%s",
        product_id,
        len(rows),
        eligible,
    )
    return eligible


async def rebuild_finance_for_products(
    deps: FinanceAutoSyncDeps,
    *,
    product_ids: Sequence[tuple[int, int]],
    merchant_code: Optional[str] = None,
) -> AutoRebuildStats:
    """``product_ids`` is sequence of (product_id, merchant_id)."""

    attempted = 0
    synced = 0
    eligible_total = 0
    skipped = 0
    errors = 0
    for product_id, merchant_id in product_ids:
        attempted += 1
        try:
            n = await rebuild_finance_for_product(
                deps,
                product_id=int(product_id),
                merchant_id=int(merchant_id),
                merchant_code=merchant_code,
            )
            if n == 0:
                # Still counts as synced attempt with empty/ineligible options.
                synced += 1
            else:
                synced += 1
                eligible_total += n
        except Exception:  # noqa: BLE001
            errors += 1
            logger.exception(
                "finance rebuild failed product_id=%s merchant_id=%s",
                product_id,
                merchant_id,
            )
    return AutoRebuildStats(
        products_attempted=attempted,
        products_synced=synced,
        eligible_options=eligible_total,
        skipped=skipped,
        errors=errors,
    )


async def rebuild_finance_for_merchant(
    deps: FinanceAutoSyncDeps,
    *,
    merchant_id: int,
    merchant_code: Optional[str] = None,
    limit: int = 200,
) -> AutoRebuildStats:
    products = await deps.product_catalog.list_products(
        merchant_id=int(merchant_id), limit=limit
    )
    pairs = [(int(p.id), int(merchant_id)) for p in products]
    return await rebuild_finance_for_products(
        deps, product_ids=pairs, merchant_code=merchant_code
    )


async def rebuild_finance_for_merchant_codes(
    deps: FinanceAutoSyncDeps,
    merchant_codes: Sequence[str],
    *,
    limit_per_merchant: int = 200,
) -> AutoRebuildStats:
    attempted = 0
    synced = 0
    eligible_total = 0
    skipped = 0
    errors = 0
    for code in merchant_codes:
        mid = await _merchant_id_for_code(deps, code)
        if mid is None:
            skipped += 1
            continue
        stats = await rebuild_finance_for_merchant(
            deps,
            merchant_id=mid,
            merchant_code=code,
            limit=limit_per_merchant,
        )
        attempted += stats.products_attempted
        synced += stats.products_synced
        eligible_total += stats.eligible_options
        errors += stats.errors
    return AutoRebuildStats(
        products_attempted=attempted,
        products_synced=synced,
        eligible_options=eligible_total,
        skipped=skipped,
        errors=errors,
    )


async def rebuild_after_campaign_feed(
    deps: FinanceAutoSyncDeps,
    *,
    merchant_codes: Sequence[str],
    limit_per_merchant: int = 200,
) -> AutoRebuildStats:
    """After campaign activate — rebuild products for linked merchants.

    Empty merchant_codes → rebuild all merchants that have catalog products
    (platform-wide campaigns).
    """

    codes = [c for c in merchant_codes if c]
    if codes:
        return await rebuild_finance_for_merchant_codes(
            deps, codes, limit_per_merchant=limit_per_merchant
        )

    directory = deps.merchant_directory
    if directory is None:
        return AutoRebuildStats(0, 0, 0, 0, 0)
    list_fn = getattr(directory, "list_active", None)
    if not callable(list_fn):
        return AutoRebuildStats(0, 0, 0, 0, 0)
    attempted = 0
    synced = 0
    eligible_total = 0
    errors = 0
    for entry in await list_fn(limit=200):
        stats = await rebuild_finance_for_merchant(
            deps,
            merchant_id=int(entry.id),
            merchant_code=str(entry.merchant_code),
            limit=limit_per_merchant,
        )
        attempted += stats.products_attempted
        synced += stats.products_synced
        eligible_total += stats.eligible_options
        errors += stats.errors
    return AutoRebuildStats(
        products_attempted=attempted,
        products_synced=synced,
        eligible_options=eligible_total,
        skipped=0,
        errors=errors,
    )


def finance_deps_from_container(container: Any) -> Optional[FinanceAutoSyncDeps]:
    extras = getattr(container, "extras", None) or {}
    index = extras.get("finance_option_index")
    catalog = extras.get("product_catalog")
    if index is None or catalog is None:
        return None
    return FinanceAutoSyncDeps(
        finance_index=index,
        product_catalog=catalog,
        merchant_directory=extras.get("merchant_directory"),
        campaign_catalog=extras.get("campaign_catalog"),
        db_pool=extras.get("pool"),
    )


__all__ = [
    "AutoRebuildStats",
    "FinanceAutoSyncDeps",
    "finance_deps_from_container",
    "rebuild_after_campaign_feed",
    "rebuild_finance_for_merchant",
    "rebuild_finance_for_merchant_codes",
    "rebuild_finance_for_product",
    "rebuild_finance_for_products",
]
