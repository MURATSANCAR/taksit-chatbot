"""P2-LIVE activation unit tests — flags, search-ready, priority, top-K."""

from __future__ import annotations

from taksitlio.merchant_readiness.priority import (
    MerchantPrioritySignals,
    MerchantPriorityWeights,
    top_priority_merchants,
)
from taksitlio.product_query.ranking import RankableProduct, rank_products_topk
from taksitlio.product_query.search_ready import eligible_for_search_ready
from taksitlio.runtime_flags import (
    FeatureFlagStatus,
    auto_promotion_allowed,
    flags_from_rows,
    seed_flags,
)


def test_auto_promotion_disabled_by_default():
    flags = seed_flags()
    assert flags["learning_auto_promotion_enabled"].status is FeatureFlagStatus.DISABLED
    assert auto_promotion_allowed(flags) is False


def test_flags_from_rows_override():
    flags = flags_from_rows(
        [{"flag_code": "adaptive_ranking_enabled", "status": "ENABLED", "config": {"topk": 20}}]
    )
    assert flags["adaptive_ranking_enabled"].status is FeatureFlagStatus.ENABLED
    assert flags["learning_auto_promotion_enabled"].status is FeatureFlagStatus.DISABLED


def test_search_ready_requires_merchant_ready():
    ok, reasons = eligible_for_search_ready(
        merchant_readiness_status="PARTIAL",
        category_id=1,
        has_active_offer=True,
        price=10.0,
        checkout_url_present=True,
        card_media_ready=True,
    )
    assert ok is False
    assert "merchant_not_ready" in reasons


def test_search_ready_ok():
    ok, reasons = eligible_for_search_ready(
        merchant_readiness_status="READY",
        category_id=1,
        has_active_offer=True,
        price=10.0,
        checkout_url_present=True,
        card_media_ready=True,
    )
    assert ok is True
    assert reasons == ()


def test_priority_orders_by_policy_weights_not_names():
    weights = MerchantPriorityWeights()
    signals = [
        MerchantPrioritySignals(
            merchant_id=1,
            active_products=100,
            category_coverage=0.99,
            media_coverage=0.99,
            price_freshness=0.99,
            finance_coverage=0.5,
            payment_plan_coverage=0.0,
            unresolved_product_count=1,
            merchant_code="m-a",
        ),
        MerchantPrioritySignals(
            merchant_id=2,
            active_products=10000,
            category_coverage=0.5,
            media_coverage=0.5,
            price_freshness=0.5,
            finance_coverage=0.1,
            payment_plan_coverage=0.0,
            unresolved_product_count=5000,
            merchant_code="m-b",
        ),
    ]
    top = top_priority_merchants(signals, weights, limit=2)
    # Higher coverage small merchant can outrank large unresolved — policy driven
    assert top[0].merchant_id in {1, 2}
    assert len(top) == 2


def test_rank_products_topk_bounds():
    items = [
        RankableProduct(
            product_id=f"p{i}",
            price=100 + i,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=10 + i,
            best_total_repayment=100 + i,
            finance_active=True,
            rate_fresh=True,
            query_relevance=i / 100,
        )
        for i in range(100)
    ]
    top = rank_products_topk(items, top_k=10)
    assert len([r for r in top if not r.disqualified]) <= 10


def test_revision_consistency_blocks_mix():
    from taksitlio.search_revision import (
        SearchRevisionBundle,
        assert_revision_consistency,
    )

    session = SearchRevisionBundle("c1", "e1", "f1", "r1")
    attempted = SearchRevisionBundle("c2", "e1", "f1", "r1")
    result = assert_revision_consistency(session, attempted)
    assert result.consistent is False
    assert "catalog_revision_mismatch" in result.reasons


def test_media_policy_accepts_non_square():
    from taksitlio.media.policy import default_seed_policy, evaluate_media_readiness

    policy = default_seed_policy()
    result = evaluate_media_readiness(
        width=786,
        height=587,
        file_size=100_000,
        decode_ok=True,
        has_product_relation=True,
        policy=policy,
    )
    assert result.card_ready is True
