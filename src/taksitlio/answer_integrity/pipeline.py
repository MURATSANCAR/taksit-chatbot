"""End-to-end answer integrity pipeline (ADR-012)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from taksitlio.answer_integrity.claim_validator import validate_claims
from taksitlio.answer_integrity.eligibility import FinanceEligibilityState
from taksitlio.answer_integrity.facts import FactEnvelope
from taksitlio.answer_integrity.response_composer import (
    ComposedResponse,
    compose_from_facts,
    merge_optional_llm_fields,
)
from taksitlio.answer_integrity.truth_status import ResponseOutcome
from taksitlio.recommendation_safety.recommendation import why_recommended


@dataclass(frozen=True)
class IntegrityPipelineResult:
    response: ComposedResponse
    gate_diagnostics: Mapping[str, Any]


# Alias kept for grounded response / older drafts
GroundedAnswer = IntegrityPipelineResult


def run_answer_integrity_pipeline(
    envelope: FactEnvelope,
    *,
    need_description: str = "ihtiyacınız",
    eligibility: Optional[FinanceEligibilityState] = None,
    reason_codes: Sequence[str] = (),
    cards: Sequence[Mapping[str, Any]] = (),
    llm_fields: Optional[Mapping[str, str]] = None,
    ranking_winner_ids: Sequence[str] = (),
    result_institution_names: Sequence[str] = (),
) -> IntegrityPipelineResult:
    """
    Backend facts → composer → optional LLM fields → Final Claim Validator.

    On claim failure, deterministic template is returned (LLM text dropped).
    """

    reasons = [why_recommended(reason_codes)] if reason_codes else []
    base = compose_from_facts(
        envelope,
        need_description=need_description,
        eligibility=eligibility,
        reason_explanations=reasons,
        cards=cards,
    )
    final = base
    if llm_fields:
        final = merge_optional_llm_fields(
            base,
            llm_fields,
            envelope=envelope,
            ranking_winner_ids=ranking_winner_ids,
            result_institution_names=result_institution_names,
        )
    cv = validate_claims(
        final.text,
        envelope,
        ranking_winner_ids=ranking_winner_ids,
        result_institution_names=result_institution_names,
    )
    if cv.failed and final.used_llm:
        final = base
        cv = validate_claims(
            final.text,
            envelope,
            ranking_winner_ids=ranking_winner_ids,
            result_institution_names=result_institution_names,
        )

    return IntegrityPipelineResult(
        response=final,
        gate_diagnostics={
            "claim_outcome": cv.outcome,
            "violations": [v.code for v in cv.violations],
            "response_outcome": final.outcome.value,
            "template_used": final.template_used,
            "fact_ids": list(envelope.fact_ids()),
        },
    )


def compose_grounded_answer(
    envelope: FactEnvelope,
    *,
    need_description: str = "ihtiyacınız",
    cards: Sequence[Mapping[str, Any]] = (),
    best_label_allowed: bool = False,
    stock_status: Optional[str] = None,
    rate_type: Optional[str] = None,
    fees_total: float = 0.0,
    clarifications: Sequence[str] = (),
    llm_decoration: Optional[Mapping[str, object]] = None,
    cost_kind: Optional[str] = None,
) -> ComposedResponse:
    """Deterministic grounded reply for product cards (chat path)."""

    _ = stock_status, rate_type, fees_total, cost_kind
    ranking_winner_ids: tuple[str, ...] = ()
    if best_label_allowed and envelope.ranking_winner_product_id:
        ranking_winner_ids = (envelope.ranking_winner_product_id,)
    base = compose_from_facts(
        envelope,
        need_description=need_description,
        cards=cards,
        clarifications=clarifications,
    )
    if llm_decoration:
        return merge_optional_llm_fields(
            base,
            {k: str(v) for k, v in llm_decoration.items() if v is not None},
            envelope=envelope,
            ranking_winner_ids=ranking_winner_ids,
        )
    cv = validate_claims(
        base.text,
        envelope,
        ranking_winner_ids=ranking_winner_ids,
    )
    if cv.failed:
        return ComposedResponse(
            text=base.text,
            outcome=ResponseOutcome.CLAIM_VALIDATION_FAILED,
            template_used="claim_failed_fallback",
            fact_ids=base.fact_ids,
            allowed_facts=base.allowed_facts,
            reason_explanation=base.reason_explanation,
            grounded=True,
            claim_validation=cv,
            claim_reasons=cv.reasons,
        )
    return ComposedResponse(
        text=base.text,
        outcome=base.outcome,
        template_used=base.template_used,
        fact_ids=base.fact_ids,
        allowed_facts=base.allowed_facts,
        reason_explanation=base.reason_explanation,
        grounded=True,
        claim_reasons=base.claim_reasons,
        claim_validation=cv,
    )


__all__ = [
    "GroundedAnswer",
    "IntegrityPipelineResult",
    "compose_grounded_answer",
    "run_answer_integrity_pipeline",
]
