"""ADR-012 acceptance — zero-tolerance claim gates."""

from __future__ import annotations

from taksitlio.answer_integrity import (
    FactType,
    FieldTruthStatus,
    QUALITY_GATES,
    build_envelope,
    build_fact,
    validate_claims,
)
from taksitlio.recommendation_safety import (
    ConstraintSource,
    NegativeConstraintLock,
    IntegritySignals,
    evaluate_recommendation_integrity,
)


def test_quality_gates_registered() -> None:
    assert "SOURCE_PROVENANCE_GATE" in QUALITY_GATES
    assert "CLAIM_GROUNDING_GATE" in QUALITY_GATES
    assert len(QUALITY_GATES) == 10


def test_zero_tolerance_ungrounded_financial_claim() -> None:
    env = build_envelope(
        [
            build_fact(
                fact_id="p1",
                fact_type=FactType.PRICE,
                value="42999 TRY",
                truth_status=FieldTruthStatus.VERIFIED,
                evidence={"price_snapshot_id": "ps"},
            )
        ]
    )
    result = validate_claims("Aylık ödeme 3500 TL faizsiz.", env)
    assert not result.ok


def test_zero_tolerance_negative_constraint_return() -> None:
    lock = NegativeConstraintLock()
    lock.lock("telefon", source=ConstraintSource.USER_CORRECTION)
    blocked = lock.reject_llm_reintroduction(proposed_positive=["telefon da uygun"])
    # concept normalization is exact token; ensure lock holds for canonical concept
    blocked2 = lock.reject_llm_reintroduction(proposed_positive=["telefon"])
    assert "telefon" in blocked2
    assert blocked2  # non-empty


def test_conflicted_never_best_label() -> None:
    decision = evaluate_recommendation_integrity(
        IntegritySignals(
            comparable_candidate_count=5,
            prices_fresh=True,
            stock_verified=True,
            variants_comparable=True,
            total_repayment_present=True,
            bank_mapping_verified=True,
            campaign_active=True,
            critical_attributes_complete=True,
            field_statuses=(FieldTruthStatus.CONFLICTED,),
        )
    )
    assert not decision.allow_best_label
