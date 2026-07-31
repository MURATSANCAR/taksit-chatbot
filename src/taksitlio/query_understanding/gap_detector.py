"""Gap detector after fast parse (ADR-011 §7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from taksitlio.query_understanding.fast_parser import FastParseResult


class UncertaintyReason(str):
    MULTIPLE_CATEGORY_CANDIDATES = "MULTIPLE_CATEGORY_CANDIDATES"
    MULTIPLE_MERCHANT_CANDIDATES = "MULTIPLE_MERCHANT_CANDIDATES"
    UNKNOWN_PRODUCT_TYPE = "UNKNOWN_PRODUCT_TYPE"
    UNRESOLVED_USE_CASE = "UNRESOLVED_USE_CASE"
    CONFLICTING_CONSTRAINTS = "CONFLICTING_CONSTRAINTS"
    MISSING_BUDGET = "MISSING_BUDGET"
    MISSING_FINANCE_CONTEXT = "MISSING_FINANCE_CONTEXT"
    MISSING_MERCHANT_FOR_BANK_QUERY = "MISSING_MERCHANT_FOR_BANK_QUERY"
    ABSTRACT_QUALITY_PREFERENCE = "ABSTRACT_QUALITY_PREFERENCE"
    COMPARISON_CRITERIA_MISSING = "COMPARISON_CRITERIA_MISSING"
    CORRECTION_AMBIGUOUS = "CORRECTION_AMBIGUOUS"


@dataclass(frozen=True)
class Uncertainty:
    field: str
    reason_code: str
    candidate_values: tuple[Any, ...] = ()
    confidence: float = 0.0
    candidate_gap: Optional[float] = None
    can_clarification_resolve: bool = True
    expected_information_gain: float = 0.0


@dataclass
class GapAnalysis:
    uncertainties: list[Uncertainty] = field(default_factory=list)
    confidence_band: str = "LOW"  # HIGH | MEDIUM | LOW
    clarification_viable: bool = False
    requires_llm: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "uncertainties": [
                {
                    "field": u.field,
                    "reason_code": u.reason_code,
                    "candidate_values": list(u.candidate_values),
                    "confidence": u.confidence,
                    "candidate_gap": u.candidate_gap,
                    "can_clarification_resolve": u.can_clarification_resolve,
                    "expected_information_gain": u.expected_information_gain,
                }
                for u in self.uncertainties
            ],
            "confidence_band": self.confidence_band,
            "clarification_viable": self.clarification_viable,
            "requires_llm": self.requires_llm,
        }


def detect_gaps(
    parse: FastParseResult,
    *,
    category_candidates: Optional[list[dict[str, Any]]] = None,
) -> GapAnalysis:
    uncertainties: list[Uncertainty] = []
    cats = parse.positive_categories
    brands = parse.brands

    if not cats and brands:
        # e.g. "Apple almak istiyorum"
        candidates = tuple(category_candidates or ())
        uncertainties.append(
            Uncertainty(
                field="category",
                reason_code=UncertaintyReason.MULTIPLE_CATEGORY_CANDIDATES
                if candidates
                else UncertaintyReason.UNKNOWN_PRODUCT_TYPE,
                candidate_values=tuple(c.get("id") or c.get("display_name") for c in candidates)
                if candidates
                else (),
                confidence=parse.confidence,
                can_clarification_resolve=True,
                expected_information_gain=0.92,
            )
        )
    elif not cats and not brands:
        if parse.usage_contexts or parse.preferences:
            uncertainties.append(
                Uncertainty(
                    field="product_type",
                    reason_code=UncertaintyReason.UNRESOLVED_USE_CASE,
                    confidence=parse.confidence,
                    can_clarification_resolve=True,
                    expected_information_gain=0.88,
                )
            )
        else:
            uncertainties.append(
                Uncertainty(
                    field="product_type",
                    reason_code=UncertaintyReason.UNKNOWN_PRODUCT_TYPE,
                    confidence=parse.confidence,
                    can_clarification_resolve=True,
                    expected_information_gain=0.85,
                )
            )

    if parse.merchant and not parse.merchant.resolved_id:
        uncertainties.append(
            Uncertainty(
                field="merchant",
                reason_code=UncertaintyReason.MULTIPLE_MERCHANT_CANDIDATES,
                confidence=parse.merchant.confidence,
                can_clarification_resolve=True,
                expected_information_gain=0.7,
            )
        )

    if parse.preferred_institutions and not parse.merchant and not cats:
        uncertainties.append(
            Uncertainty(
                field="merchant",
                reason_code=UncertaintyReason.MISSING_MERCHANT_FOR_BANK_QUERY,
                confidence=parse.confidence,
                can_clarification_resolve=True,
                expected_information_gain=0.6,
            )
        )

    # Abstract quality without concrete product type
    if parse.requires_llm:
        uncertainties.append(
            Uncertainty(
                field="need_semantics",
                reason_code=UncertaintyReason.ABSTRACT_QUALITY_PREFERENCE,
                confidence=parse.confidence,
                can_clarification_resolve=False,
                expected_information_gain=0.3,
            )
        )

    # Budget is NOT required if category+brand clear ("30 bin liraya Samsung telefon")
    # Only flag missing budget when finance intent without any number and vague product
    if parse.intent == "PRODUCT_WITH_FINANCE" and not parse.budget and not parse.requested_terms:
        pass  # optional — do not force budget question

    if parse.confidence >= 0.90 and not uncertainties:
        band = "HIGH"
    elif parse.confidence >= 0.90 and all(u.can_clarification_resolve for u in uncertainties) is False:
        band = "HIGH" if not uncertainties else "MEDIUM"
    elif parse.confidence >= 0.75 and cats and (brands or parse.budget or parse.attributes):
        band = "HIGH"
    elif any(u.can_clarification_resolve and u.expected_information_gain >= 0.75 for u in uncertainties):
        band = "MEDIUM"
    elif parse.confidence >= 0.78 and not uncertainties:
        band = "HIGH"
    else:
        band = "LOW" if parse.confidence < 0.70 or parse.requires_llm else "MEDIUM"

    # Re-evaluate HIGH when category+budget+brand present even with soft uncertainties empty
    if cats and parse.budget and (brands or parse.attributes or parse.requested_terms):
        if not any(u.reason_code == UncertaintyReason.UNKNOWN_PRODUCT_TYPE for u in uncertainties):
            band = "HIGH"
            # Drop soft unknowns
            uncertainties = [
                u
                for u in uncertainties
                if u.reason_code
                not in {
                    UncertaintyReason.UNKNOWN_PRODUCT_TYPE,
                    UncertaintyReason.UNRESOLVED_USE_CASE,
                }
            ]

    if cats and brands and not uncertainties:
        band = "HIGH"

    clarification_viable = band == "MEDIUM" and any(
        u.can_clarification_resolve for u in uncertainties
    )
    requires_llm = band == "LOW" and (
        parse.requires_llm or not clarification_viable or not any(u.can_clarification_resolve for u in uncertainties)
    )
    # If MEDIUM with clarification, do not require LLM
    if clarification_viable:
        requires_llm = False

    return GapAnalysis(
        uncertainties=uncertainties,
        confidence_band=band,
        clarification_viable=clarification_viable,
        requires_llm=requires_llm,
    )
