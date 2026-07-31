"""ADR-012 last-mile wiring: sponsored / negation / drift-breaker / evidence."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from taksitlio.chatbot_cards import ProductCardFinanceSummary, card_to_public_dict, build_product_card
from taksitlio.chatbot_cards import CardSourceProduct
from taksitlio.progressive_results import build_partial_snapshot, score_partial_candidate
from taksitlio.product_query.ranking import RankingMode
from taksitlio.product_query.search import (
    ProductSearchRequest,
    SearchProductCandidate,
    search_products,
)
from taksitlio.recommendation_safety import BreakerAction, QualityCircuitBreaker
from taksitlio.answer_integrity.policy_store import InMemoryCircuitBreakerStore


def _candidate(**kwargs: object) -> SearchProductCandidate:
    base = dict(
        product_id="p1",
        display_name="Laptop 16GB",
        brand_model="X / 16",
        merchant_id="m1",
        merchant_display_name="Merchant",
        price=10000.0,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
        has_primary_image=True,
        thumbnail_cdn_url="https://cdn.test/x.webp",
        offer_id="42",
        price_snapshot_id="offer:42",
        stock_snapshot_id="offer:42:stock",
    )
    base.update(kwargs)
    return SearchProductCandidate(**base)  # type: ignore[arg-type]


def test_search_uses_sponsored_isolation_and_evidence_ids() -> None:
    async def _run() -> None:
        products = [
            _candidate(product_id="organic", price=100.0, query_relevance=1.0),
            _candidate(
                product_id="sponsor",
                price=200.0,
                query_relevance=0.2,
                is_sponsored=True,
                sponsor_weight=99.0,
            ),
            _candidate(product_id="mid", price=150.0, query_relevance=0.8),
        ]
        req = ProductSearchRequest(
            utterance="laptop",
            ranking_mode=RankingMode.BEST_OVERALL_VALUE,
            phase="FINANCE_ENRICHED",
            sponsored_product_ids=("sponsor",),
            sponsored_weights={"sponsor": 99.0},
        )
        # Need finance fields for BEST_OVERALL_VALUE not to exclude
        products = [
            replace(
                p,
                finance_active=True,
                rate_fresh=True,
                campaign_active=True,
                best_monthly_payment=100.0,
                best_total_repayment=1200.0,
                best_term_months=12,
                card_finance=ProductCardFinanceSummary(
                    institution_display_name="Bank",
                    term_months=12,
                    monthly_payment=100.0,
                    total_repayment=1200.0,
                    display_label="Tahmini aylık ödeme",
                    payment_calculation_id="pay:r1:12",
                    rate_snapshot_id="r1",
                    campaign_version_id="c1",
                    merchant_finance_agreement_id="agr:m1:b1",
                ),
            )
            for p in products
        ]
        resp = await search_products(req, products=products)
        cards = [card_to_public_dict(c) for c in resp.result_phase.cards]
        assert cards
        assert cards[0].get("price_snapshot_id")
        assert cards[0]["product_id"] != "sponsor" or cards[0].get("ranking_label") == "Sponsorlu seçenek"
        finance = cards[0].get("best_finance")
        if finance:
            assert finance.get("payment_calculation_id")

    asyncio.run(_run())


def test_price_disabled_merchant_filtered_out() -> None:
    async def _run() -> None:
        products = [
            _candidate(product_id="a", merchant_id="bad", price=100.0),
            _candidate(product_id="b", merchant_id="good", price=110.0),
        ]
        products = [
            replace(
                p,
                finance_active=False,
                has_primary_image=True,
            )
            for p in products
        ]
        req = ProductSearchRequest(
            utterance="laptop",
            ranking_mode=RankingMode.CHEAPEST_PRODUCT_PRICE,
            price_disabled_merchant_ids=frozenset({"bad"}),
        )
        resp = await search_products(req, products=products)
        ids = {c.product_id for c in resp.result_phase.cards}
        assert "a" not in ids
        assert "b" in ids

    asyncio.run(_run())


def test_partial_snapshot_hard_excludes_negatives() -> None:
    products = [
        {"product_id": "1", "display_name": "iPhone telefon", "price": 10, "stock_status": "AVAILABLE", "price_freshness": "FRESH", "has_primary_image": True, "query_relevance": 0.9},
        {"product_id": "2", "display_name": "MacBook laptop", "price": 20, "stock_status": "AVAILABLE", "price_freshness": "FRESH", "has_primary_image": True, "query_relevance": 0.8},
    ]
    constraints = {
        "positive_categories": [{"display_name": "laptop"}],
        "negative_categories": [{"display_name": "telefon"}],
        "safe_to_retrieve": True,
    }
    assert score_partial_candidate(products[0], constraints) == float("-inf")
    snap = build_partial_snapshot(query_version=1, products=products, constraints=constraints)
    ids = [p.product_id for p in snap.products]
    assert "1" not in ids
    assert "2" in ids


def test_card_public_dict_carries_evidence() -> None:
    card = build_product_card(
        CardSourceProduct(
            product_id="p1",
            display_name="Laptop",
            brand_model="X",
            merchant_display_name="M",
            price=1000,
            stock_status="AVAILABLE",
            has_primary_image=True,
            thumbnail_cdn_url="https://cdn.test/a.webp",
            price_snapshot_id="ps1",
            stock_snapshot_id="ss1",
            best_finance=ProductCardFinanceSummary(
                institution_display_name="Bank",
                term_months=12,
                monthly_payment=100,
                total_repayment=1200,
                display_label="Tahmini aylık ödeme",
                payment_calculation_id="pay1",
                rate_snapshot_id="rs1",
            ),
        ),
        include_finance=True,
    )
    public = card_to_public_dict(card)
    assert public["price_snapshot_id"] == "ps1"
    assert public["stock_snapshot_id"] == "ss1"
    assert public["best_finance"]["payment_calculation_id"] == "pay1"
    assert public["best_finance"]["rate_snapshot_id"] == "rs1"


def test_circuit_breaker_store_gates_chat_bridge_helper() -> None:
    from taksitlio.product_query.chat_bridge import _price_disabled_merchant_ids

    store = InMemoryCircuitBreakerStore()
    cb = QualityCircuitBreaker(broken_price_rate=0.2)
    cb.evaluate()
    store.breakers["m9"] = cb
    assert "m9" in _price_disabled_merchant_ids(store)
    assert BreakerAction.DISABLE_PRICE_RESULTS in cb.disabled
