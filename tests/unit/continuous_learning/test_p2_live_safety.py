"""Unit tests for Recovery P2-LIVE learning safety and readiness gates."""

from __future__ import annotations

import pytest

from taksitlio.catalog_events import (
    CatalogDomainEvent,
    CatalogEventType,
    ProjectionKind,
    plan_selective_refresh,
)
from taksitlio.continuous_learning import (
    AliasObservation,
    LearningStatus,
    NumericExtraction,
    PromotionThresholds,
    TenantLearningScope,
    detect_taxonomy_drift,
    evaluate_promotion_gate,
    observe_alias,
    record_user_correction,
    validate_numeric_extraction,
)
from taksitlio.continuous_learning.lifecycle import (
    LearningCandidateView,
    assert_not_direct_promoted,
)
from taksitlio.continuous_learning.taxonomy import (
    SourceTaxonomyNodeRef,
    can_auto_publish,
    create_candidate,
    score_taxonomy_candidate,
)
from taksitlio.media.policy import default_seed_policy, evaluate_media_readiness
from taksitlio.merchant_readiness import (
    MerchantCoverageMetrics,
    MerchantReadinessStatus,
    ReadinessThresholds,
    evaluate_merchant_readiness,
    recover_from_degraded,
)
from taksitlio.product_query.ranking import RankableProduct
from taksitlio.ranking_adaptation import (
    RankingPolicyVersion,
    assert_safety_floor_preserved,
    evaluate_promotion_gate as ranking_gate,
    shadow_compare,
)


def test_no_direct_promoted_creation():
    with pytest.raises(ValueError):
        assert_not_direct_promoted(LearningStatus.PROMOTED)


def test_single_observation_alias_not_promoted():
    obs = AliasObservation(
        raw_token="Teknoksa",
        normalized_token="teknoksa",
        entity_type="MERCHANT",
        resolved_entity_id="m-teknosa",
        resolution_confidence=0.92,
        top1_top2_gap=0.4,
        match_method="trigram",
        context="merchant_search",
    )
    state = observe_alias(None, obs)
    assert state is not None
    assert state.learning_status is LearningStatus.OBSERVED
    assert state.observation_count == 1
    thr = PromotionThresholds(allow_single_observation_promote=False)
    view = LearningCandidateView(
        learning_status=LearningStatus.SHADOW,
        confidence=0.99,
        candidate_gap=0.5,
        observation_count=1,
        positive_evidence=1,
    )
    decision = evaluate_promotion_gate(view, thr, target=LearningStatus.PROMOTED)
    assert decision.allowed is False
    assert "single_observation_promotion_forbidden" in decision.reasons


def test_user_correction_produces_negative_and_positive_evidence():
    from taksitlio.continuous_learning.alias import AliasCandidateState

    rejected = AliasCandidateState(
        entity_type="MERCHANT",
        observed_alias="teknosa",
        normalized_alias="teknosa",
        candidate_entity_id="m-teknosa",
        learning_status=LearningStatus.CANDIDATE,
        observation_count=3,
        positive_evidence=3,
    )
    confirmed = AliasCandidateState(
        entity_type="MERCHANT",
        observed_alias="mediamarkt",
        normalized_alias="mediamarkt",
        candidate_entity_id="m-mediamarkt",
        learning_status=LearningStatus.OBSERVED,
        observation_count=1,
        positive_evidence=1,
    )
    rejected, confirmed = record_user_correction(
        rejected_state=rejected,
        confirmed_state=confirmed,
        rejected_entity_id="m-teknosa",
        confirmed_entity_id="m-mediamarkt",
    )
    assert rejected.negative_evidence == 1
    assert confirmed.positive_evidence == 2
    assert confirmed.learning_status is LearningStatus.CANDIDATE


def test_non_anonymized_user_pref_not_global():
    obs = AliasObservation(
        raw_token="x",
        normalized_token="x",
        entity_type="MERCHANT",
        resolved_entity_id="m-1",
        resolution_confidence=0.9,
        top1_top2_gap=0.2,
        match_method="exact",
        context="pref",
        tenant_scope=TenantLearningScope.USER_PREFERENCE_MEMORY,
        anonymized=False,
    )
    assert observe_alias(None, obs) is None


def test_numeric_ram_vs_storage_and_inch():
    bad = validate_numeric_extraction(
        NumericExtraction(
            attribute_code="ram",
            raw_value="65 inch",
            normalized_value=65.0,
            unit_code="inch",
            confidence=0.99,
            evidence_span="65 inch",
        )
    )
    assert bad.accepted is False
    assert bad.usable_in_required_filter is False

    ok = validate_numeric_extraction(
        NumericExtraction(
            attribute_code="ram",
            raw_value="16 GB",
            normalized_value=16.0,
            unit_code="gb",
            confidence=0.99,
            evidence_span="16 GB",
        )
    )
    assert ok.accepted is True
    assert ok.usable_in_required_filter is True

    low = validate_numeric_extraction(
        NumericExtraction(
            attribute_code="storage",
            raw_value="512 GB",
            normalized_value=512.0,
            unit_code="gb",
            confidence=0.5,
            evidence_span="512 GB",
        )
    )
    assert low.accepted is True
    assert low.usable_in_required_filter is False


def test_taxonomy_requires_gap_and_no_conflict():
    conf, gap, consistency = score_taxonomy_candidate(
        exact_mapping_hit=True,
        normalized_alias_hit=True,
        sibling_parent_agreement=0.9,
        sample_title_consistency=0.95,
        brand_distribution_consistency=0.9,
        negative_category_conflicts=0,
        historic_stability=0.9,
    )
    node = SourceTaxonomyNodeRef(1, 1, 1, "a/b", "a/b")
    cand = create_candidate(
        node,
        99,
        match_method="exact",
        confidence=conf,
        candidate_gap=gap,
        sample_consistency=consistency,
        conflict_count=0,
        evidence=[],
    )
    # zero observations → cannot publish
    thr = PromotionThresholds(
        minimum_observations=10,
        minimum_confidence=0.9,
        minimum_candidate_gap=0.2,
        minimum_sample_consistency=0.85,
    )
    ok, reasons = can_auto_publish(cand, thr)
    assert ok is False
    assert "insufficient_observations" in reasons


def test_selective_refresh_not_full_catalog():
    events = [
        CatalogDomainEvent(
            event_type=CatalogEventType.PRICE_CHANGED,
            product_id=42,
            merchant_id=7,
            content_hash="abc",
        )
    ]
    plan = plan_selective_refresh(events)
    assert plan.full_catalog_rebuild is False
    assert plan.product_ids == frozenset({42})
    assert ProjectionKind.FINANCE in plan.projections
    assert ProjectionKind.SEARCH in plan.projections


def test_merchant_readiness_auto_degrade():
    thr = ReadinessThresholds()
    ready_metrics = MerchantCoverageMetrics(
        active_products=1000,
        searchable_products=1000,
        category_coverage=0.96,
        brand_coverage=0.95,
        attribute_coverage=0.95,
        stock_coverage=0.95,
        card_media_coverage=0.96,
        fresh_price_coverage=0.96,
        valid_url_coverage=0.995,
        finance_coverage=0.5,
        payment_plan_coverage=0.5,
        golden_pass_rate=1.0,
    )
    d1 = evaluate_merchant_readiness(ready_metrics, thr)
    assert d1.status is MerchantReadinessStatus.READY

    slipped = MerchantCoverageMetrics(
        active_products=1000,
        searchable_products=1000,
        category_coverage=0.70,
        brand_coverage=0.95,
        attribute_coverage=0.95,
        stock_coverage=0.95,
        card_media_coverage=0.96,
        fresh_price_coverage=0.96,
        valid_url_coverage=0.995,
        finance_coverage=0.5,
        payment_plan_coverage=0.5,
        golden_pass_rate=1.0,
    )
    d2 = evaluate_merchant_readiness(
        slipped, thr, previous_status=MerchantReadinessStatus.READY
    )
    assert d2.status is MerchantReadinessStatus.DEGRADED
    assert d2.include_in_search is False

    still = recover_from_degraded(slipped, thr)
    assert still.status is MerchantReadinessStatus.DEGRADED


def test_ranking_cannot_resurrect_and_gate_blocks_latency():
    items = [
        RankableProduct(
            product_id="good",
            price=100,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=10,
            best_total_repayment=100,
            finance_active=True,
            rate_fresh=True,
        ),
        RankableProduct(
            product_id="bad",
            price=50,
            stock_status="OUT_OF_STOCK",
            price_freshness="STALE",
            has_primary_image=False,
            finance_active=False,
            rate_fresh=False,
        ),
    ]
    champ = RankingPolicyVersion.from_weight_map(
        policy_code="p", version=1, role="CHAMPION", weights={}
    )
    chall = RankingPolicyVersion.from_weight_map(
        policy_code="p", version=2, role="CHALLENGER", weights={"price": 0.5}
    )
    shadow = shadow_compare(items, champ, chall)
    assert shadow["mode"] == "SHADOW"
    assert shadow["safety_floor_ok"] is True

    from taksitlio.product_query.ranking import RankedProduct

    ok, reasons = assert_safety_floor_preserved(
        eligible_before=items,
        ranked_after=(
            RankedProduct("bad", 1.0, "x", False, ()),
        ),
    )
    assert ok is False
    assert reasons

    gate = ranking_gate(
        quality_regression=False,
        wrong_finance_result=False,
        negative_constraint_leakage=False,
        latency_p95_ms=105.0,
        feedback_sample_count=200,
    )
    assert gate.promote is False
    assert "latency_target_missed" in gate.reasons


def test_taxonomy_drift_freezes_mappings():
    baseline = {"ayakkabi": 0.8, "canta": 0.2}
    observed = {"ayakkabi": 0.1, "elektronik": 0.9}
    alarm = detect_taxonomy_drift(
        baseline_path_share=baseline,
        observed_path_share=observed,
        merchant_id=1,
    )
    assert alarm is not None
    assert alarm.freeze_new_mappings is True
    assert alarm.preserve_validated_mappings is True


# revision pinning covered in tests/unit/activation/test_p2_activation_units.py
