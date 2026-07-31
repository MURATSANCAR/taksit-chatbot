"""ADR-012 acceptance — zero-tolerance claim gates."""

from __future__ import annotations

from taksitlio.answer_integrity import (
    EvidenceRef,
    Fact,
    FactType,
    FieldTruthStatus,
    QUALITY_GATES,
    build_fact_envelope,
    validate_claims,
)
from taksitlio.recommendation_safety import (
    ConstraintSource,
    NegativeConstraintLock,
    RecommendationCandidate,
    evaluate_recommendation_integrity,
)


def test_quality_gates_registered() -> None:
    assert "SOURCE_PROVENANCE_GATE" in QUALITY_GATES
    assert "CLAIM_GROUNDING_GATE" in QUALITY_GATES
    assert len(QUALITY_GATES) == 10


def test_zero_tolerance_ungrounded_financial_claim() -> None:
    env = build_fact_envelope(
        [
            Fact(
                fact_id="p1",
                fact_type=FactType.PRICE,
                value="42999 TRY",
                truth_status=FieldTruthStatus.VERIFIED,
                evidence=EvidenceRef(price_snapshot_id="ps"),
            )
        ]
    )
    result = validate_claims("Aylık ödeme 3500 TL faizsiz.", env)
    assert not result.ok


def test_zero_tolerance_negative_constraint_return() -> None:
    lock = NegativeConstraintLock()
    lock.lock("telefon", source=ConstraintSource.USER_CORRECTION)
    blocked = lock.reject_llm_reintroduction(proposed_positive=["telefon"])
    assert "telefon" in blocked


def test_conflicted_never_best_label() -> None:
    cands = [
        RecommendationCandidate(
            product_id=f"p{i}",
            price_fresh=True,
            stock_verified=True,
            variants_comparable=True,
            total_repayment=1000 + i,
            monthly_payment=100,
            price=900,
            finance_mapping_verified=True,
            campaign_active=True,
            critical_attributes_complete=True,
            conflicted=(i == 0),
        )
        for i in range(3)
    ]
    decision = evaluate_recommendation_integrity(cands, winner_product_id="p0")
    assert not decision.best_label_allowed
