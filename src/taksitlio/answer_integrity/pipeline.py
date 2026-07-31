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
from taksitlio.recommendation_safety.recommendation import why_recommended


@dataclass(frozen=True)
class IntegrityPipelineResult:
    response: ComposedResponse
    gate_diagnostics: Mapping[str, Any]


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
            ranking_winner_ids=ranking_winner_ids,
            result_institution_names=result_institution_names,
        )
    # Final pass
    cv = validate_claims(
        final.text,
        envelope,
        ranking_winner_ids=ranking_winner_ids,
        result_institution_names=result_institution_names,
    )
    if cv.failed and final.used_llm:
        final = base
        cv = final.claim_validation

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


__all__ = ["IntegrityPipelineResult", "run_answer_integrity_pipeline"]
