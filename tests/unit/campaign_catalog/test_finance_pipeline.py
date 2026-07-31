"""Closed loop: campaign feed → activate → projection → best_finance."""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.campaign_catalog.feed_apply import (
    InMemoryCampaignCatalog,
    apply_campaign_feed_result,
)
from taksitlio.campaign_catalog.models import CampaignStatus
from taksitlio.campaign_catalog.term_options import build_term_options
from taksitlio.chatbot_cards import (
    CardSourceProduct,
    ResponsePhase,
    build_finance_enriched_phase,
    card_to_public_dict,
)
from taksitlio.ingestion.adapters.generic_campaign_feed import (
    GenericCampaignFeedAdapter,
    run_campaign_feed_dry,
)
from taksitlio.product_query.finance_index import (
    InMemoryFinanceOptionIndex,
    InstitutionLabelResolver,
    enrich_candidate_with_finance,
    pick_best_eligible,
)
from taksitlio.product_query.finance_projection import OfferFinanceContext
from taksitlio.product_query.finance_sync import sync_finance_from_memory_catalog
from taksitlio.product_query.search import SearchProductCandidate


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "crawler"
    / "feeds"
    / "fixtures"
    / "src-b-fibabanka.json"
)


@pytest.mark.asyncio
async def test_feed_activate_builds_term_options_for_merchant() -> None:
    adapter = GenericCampaignFeedAdapter(
        feed_path=FIXTURE, default_institution_code="fi-fibabanka"
    )
    result = await run_campaign_feed_dry(adapter)
    catalog = InMemoryCampaignCatalog()
    applied = apply_campaign_feed_result(
        catalog, result, institution_display_name="Fibabanka", activate=True
    )
    assert applied["campaigns_activated"] == 2
    assert applied["rates_applied"] == 1
    assert "m-teknosa" in catalog.agreements
    assert "fi-fibabanka" in catalog.agreements["m-teknosa"]

    zero = catalog.campaigns_by_code["fib-zero-rate-explicit"]
    assert zero.status is CampaignStatus.ACTIVE
    assert zero.agreement_active is True

    options = build_term_options(
        campaigns=tuple(catalog.campaigns_by_code.values()),
        rates=tuple(catalog.rates),
        merchant_code="m-teknosa",
        institution_ids={"fi-fibabanka": "7"},
    )
    assert len(options) == 1
    assert options[0].term_months == 12
    assert options[0].institution_id == "7"


@pytest.mark.asyncio
async def test_catalog_to_best_finance_on_card() -> None:
    adapter = GenericCampaignFeedAdapter(
        feed_path=FIXTURE, default_institution_code="fi-fibabanka"
    )
    result = await run_campaign_feed_dry(adapter)
    catalog = InMemoryCampaignCatalog()
    apply_campaign_feed_result(
        catalog, result, institution_display_name="Fibabanka", activate=True
    )

    index = InMemoryFinanceOptionIndex()
    offer = OfferFinanceContext(
        product_offer_id="100",
        merchant_id="1",
        merchant_code="m-teknosa",
        purchase_price=12000.0,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
    )
    rows = await sync_finance_from_memory_catalog(
        index, catalog, product_id="42", offer=offer
    )
    assert len(rows) == 1
    assert rows[0].eligibility_status == "ELIGIBLE"
    assert rows[0].monthly_payment == 1000.0
    assert rows[0].display_label == "Tahmini aylık ödeme"

    best = pick_best_eligible(rows)
    assert best is not None

    candidate = SearchProductCandidate(
        product_id="42",
        display_name="Laptop",
        brand_model="Brand X",
        merchant_id="1",
        merchant_display_name="Teknosa",
        price=12000.0,
        currency="TRY",
        stock_status="AVAILABLE",
        price_freshness="FRESH",
        has_primary_image=False,
        campaign_active=False,
        finance_active=False,
        rate_fresh=False,
    )
    enriched = enrich_candidate_with_finance(
        candidate,
        rows,
        institutions=InstitutionLabelResolver(labels={"fi-fibabanka": "Fibabanka"}),
    )
    assert enriched.card_finance is not None
    assert enriched.card_finance.monthly_payment == 1000.0
    assert enriched.card_finance.term_months == 12
    assert "Tahmini" in enriched.card_finance.display_label
    assert enriched.card_finance.institution_display_name == "Fibabanka"

    phase = build_finance_enriched_phase(
        [
            CardSourceProduct(
                product_id="42",
                display_name="Laptop",
                brand_model="Brand X",
                merchant_display_name="Teknosa",
                price=12000.0,
                best_finance=enriched.card_finance,
            )
        ]
    )
    assert phase.phase is ResponsePhase.FINANCE_ENRICHED
    assert len(phase.cards) == 1
    assert phase.cards[0].best_finance is not None
    public = card_to_public_dict(phase.cards[0])
    assert public["best_finance"]["monthly_payment"] == 1000.0
    assert public["best_finance"]["term_months"] == 12


@pytest.mark.asyncio
async def test_inactive_feed_does_not_project() -> None:
    adapter = GenericCampaignFeedAdapter(
        feed_path=FIXTURE, default_institution_code="fi-fibabanka"
    )
    result = await run_campaign_feed_dry(adapter)
    catalog = InMemoryCampaignCatalog()
    apply_campaign_feed_result(catalog, result, activate=False)

    index = InMemoryFinanceOptionIndex()
    offer = OfferFinanceContext(
        product_offer_id="100",
        merchant_id="1",
        merchant_code="m-teknosa",
        purchase_price=12000.0,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
    )
    rows = await sync_finance_from_memory_catalog(
        index, catalog, product_id="42", offer=offer
    )
    assert rows == ()
    assert pick_best_eligible(rows) is None
