"""Auto finance rebuild after campaign + product ingest."""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.campaign_catalog.feed_apply import (
    InMemoryCampaignCatalog,
    apply_campaign_feed_result,
)
from taksitlio.ingestion.adapters.generic_campaign_feed import (
    GenericCampaignFeedAdapter,
    run_campaign_feed_dry,
)
from taksitlio.merchant.directory import InMemoryMerchantDirectory, MerchantDirectoryEntry
from taksitlio.product.catalog import InMemoryProductCatalogRepository
from taksitlio.product.upsert import plan_offer_upsert, plan_product_upsert
from taksitlio.product_query.auto_finance import (
    FinanceAutoSyncDeps,
    rebuild_after_campaign_feed,
    rebuild_finance_for_product,
)
from taksitlio.product_query.finance_index import (
    InMemoryFinanceOptionIndex,
    pick_best_eligible,
)
from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
from taksitlio.product.models import OfferFreshness


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "crawler"
    / "feeds"
    / "fixtures"
    / "src-b-fibabanka.json"
)


def _product() -> NormalizedProduct:
    return NormalizedProduct(
        external_product_id="sku-1",
        display_name="Laptop 16GB",
        brand="Brand",
        model="X",
        description=None,
        category_path=(),
        attributes={},
        source_url=None,
        gtin=None,
        mpn=None,
        content_hash="h1",
    )


def _offer() -> NormalizedOffer:
    return NormalizedOffer(
        currency="TRY",
        price=12000.0,
        list_price=None,
        checkout_url=None,
        content_hash="o1",
    )


@pytest.mark.asyncio
async def test_campaign_then_product_auto_fills_best_finance() -> None:
    adapter = GenericCampaignFeedAdapter(
        feed_path=FIXTURE, default_institution_code="fi-fibabanka"
    )
    result = await run_campaign_feed_dry(adapter)
    campaign_catalog = InMemoryCampaignCatalog()
    apply_campaign_feed_result(
        campaign_catalog, result, institution_display_name="Fibabanka", activate=True
    )

    directory = InMemoryMerchantDirectory(
        [
            MerchantDirectoryEntry(
                id=1, merchant_code="m-teknosa", display_name="Teknosa"
            )
        ]
    )
    product_catalog = InMemoryProductCatalogRepository()
    finance_index = InMemoryFinanceOptionIndex()
    deps = FinanceAutoSyncDeps(
        finance_index=finance_index,
        product_catalog=product_catalog,
        merchant_directory=directory,
        campaign_catalog=campaign_catalog,
    )

    product_plan = plan_product_upsert(_product(), previous_content_hash=None)
    stored = await product_catalog.upsert_product(
        merchant_id=1,
        plan=product_plan,
        data_quality_status="READY",
        status="ACTIVE",
    )
    offer_plan = plan_offer_upsert(_offer(), NormalizedStock(status="AVAILABLE"), None)
    # Force FRESH freshness on plan if needed
    await product_catalog.upsert_offer(
        merchant_id=1, product_id=stored.id, plan=offer_plan
    )

    # Campaign first path: rebuild merchants after campaign
    stats = await rebuild_after_campaign_feed(deps, merchant_codes=("m-teknosa",))
    assert stats.products_synced == 1
    assert stats.eligible_options >= 1

    rows = await finance_index.list_for_product(str(stored.id))
    best = pick_best_eligible(rows)
    assert best is not None
    assert best.monthly_payment == 1000.0


@pytest.mark.asyncio
async def test_product_rebuild_alone_uses_active_catalog() -> None:
    adapter = GenericCampaignFeedAdapter(
        feed_path=FIXTURE, default_institution_code="fi-fibabanka"
    )
    result = await run_campaign_feed_dry(adapter)
    campaign_catalog = InMemoryCampaignCatalog()
    apply_campaign_feed_result(campaign_catalog, result, activate=True)

    directory = InMemoryMerchantDirectory(
        [MerchantDirectoryEntry(id=1, merchant_code="m-teknosa", display_name="Teknosa")]
    )
    product_catalog = InMemoryProductCatalogRepository()
    finance_index = InMemoryFinanceOptionIndex()
    deps = FinanceAutoSyncDeps(
        finance_index=finance_index,
        product_catalog=product_catalog,
        merchant_directory=directory,
        campaign_catalog=campaign_catalog,
    )
    product_plan = plan_product_upsert(_product(), previous_content_hash=None)
    stored = await product_catalog.upsert_product(
        merchant_id=1,
        plan=product_plan,
        data_quality_status="READY",
        status="ACTIVE",
    )
    await product_catalog.upsert_offer(
        merchant_id=1,
        product_id=stored.id,
        plan=plan_offer_upsert(_offer(), NormalizedStock(status="AVAILABLE"), None),
    )
    eligible = await rebuild_finance_for_product(
        deps, product_id=stored.id, merchant_id=1, merchant_code="m-teknosa"
    )
    assert eligible >= 1
