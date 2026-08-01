"""ADR-010 P4 — finance projection + ranking tests."""

from __future__ import annotations

from taksitlio.campaign_catalog.models import (
    CampaignStatus,
    CampaignType,
    FinanceCampaignRecord,
    RateSnapshotRecord,
    RateType,
)
from taksitlio.product_query.finance_projection import (
    InstitutionTermOption,
    OfferFinanceContext,
    rebuild_finance_options,
)
from taksitlio.product_query.ranking import RankableProduct, RankingMode, rank_products


def test_finance_projection_eligible_zero_rate() -> None:
    offer = OfferFinanceContext(
        product_offer_id="o1",
        merchant_id="1",
        merchant_code="m1",
        purchase_price=12000,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
    )
    campaign = FinanceCampaignRecord(
        campaign_code="c1",
        institution_code="bank",
        display_name="Zero",
        campaign_type=CampaignType.ZERO_RATE,
        status=CampaignStatus.ACTIVE,
        agreement_active=True,
        eligible_merchant_codes=("m1",),
        eligible_terms=(12,),
    )
    rate = RateSnapshotRecord(
        financial_product_code="fp",
        rate_type=RateType.ZERO_RATE,
        freshness_status="FRESH",
    )
    rows = rebuild_finance_options(
        offer,
        (
            InstitutionTermOption(
                institution_id="i1",
                financial_product_code="fp",
                term_months=12,
                rate_snapshot=rate,
                campaign=campaign,
                campaign_id="c1",
                rate_snapshot_id="r1",
            ),
        ),
    )
    assert rows[0].eligibility_status == "ELIGIBLE"
    assert rows[0].monthly_payment == 1000.0


def test_finance_projection_allows_unknown_stock() -> None:
    offer = OfferFinanceContext(
        product_offer_id="o1",
        merchant_id="1",
        merchant_code="m1",
        purchase_price=9000,
        stock_status="UNKNOWN",
        price_freshness="FRESH",
    )
    rate = RateSnapshotRecord(
        financial_product_code="fp",
        rate_type=RateType.ZERO_RATE,
        freshness_status="FRESH",
    )
    rows = rebuild_finance_options(
        offer,
        (
            InstitutionTermOption(
                institution_id="i1",
                financial_product_code="fp",
                term_months=9,
                rate_snapshot=rate,
            ),
        ),
    )
    assert rows[0].eligibility_status == "ELIGIBLE"
    assert rows[0].monthly_payment == 1000.0


def test_finance_projection_blocks_stale_price() -> None:
    offer = OfferFinanceContext(
        product_offer_id="o1",
        merchant_id="1",
        merchant_code="m1",
        purchase_price=12000,
        stock_status="AVAILABLE",
        price_freshness="STALE",
    )
    rate = RateSnapshotRecord(
        financial_product_code="fp",
        rate_type=RateType.ZERO_RATE,
        freshness_status="FRESH",
    )
    rows = rebuild_finance_options(
        offer,
        (
            InstitutionTermOption(
                institution_id="i1",
                financial_product_code="fp",
                term_months=12,
                rate_snapshot=rate,
            ),
        ),
    )
    assert rows[0].eligibility_status == "INELIGIBLE"
    assert "price_not_fresh" in rows[0].ineligible_reasons


def test_ranking_excludes_stale_from_best() -> None:
    items = (
        RankableProduct(
            product_id="good",
            price=1000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=100,
            best_total_repayment=1200,
            finance_active=True,
            rate_fresh=True,
            query_relevance=0.9,
            attribute_coverage=0.8,
        ),
        RankableProduct(
            product_id="stale",
            price=900,
            stock_status="AVAILABLE",
            price_freshness="STALE",
            has_primary_image=True,
            best_monthly_payment=90,
            best_total_repayment=1080,
            finance_active=True,
            rate_fresh=True,
        ),
    )
    ranked = rank_products(items, mode=RankingMode.LOWEST_MONTHLY_PAYMENT)
    assert ranked[0].product_id == "good"
    assert any(r.product_id == "stale" and r.disqualified for r in ranked)


def test_cheapest_price_mode() -> None:
    items = (
        RankableProduct(
            product_id="a",
            price=2000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            finance_active=True,
            rate_fresh=True,
        ),
        RankableProduct(
            product_id="b",
            price=1500,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            finance_active=True,
            rate_fresh=True,
        ),
    )
    ranked = rank_products(items, mode=RankingMode.CHEAPEST_PRODUCT_PRICE)
    assert ranked[0].product_id == "b"


def test_shortest_and_longest_term_modes() -> None:
    items = (
        RankableProduct(
            product_id="a",
            price=2000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=200,
            best_term_months=12,
            finance_active=True,
            rate_fresh=True,
        ),
        RankableProduct(
            product_id="b",
            price=2500,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=400,
            best_term_months=6,
            finance_active=True,
            rate_fresh=True,
        ),
    )
    short = rank_products(items, mode=RankingMode.SHORTEST_TERM)
    assert short[0].product_id == "b"
    long = rank_products(items, mode=RankingMode.LONGEST_TERM)
    assert long[0].product_id == "a"
