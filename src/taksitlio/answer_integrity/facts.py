"""Fact envelope + allowed_facts builder (ADR-012 §1 / §4).

No evidence → no claim. Every critical display field carries a provenance ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from taksitlio.answer_integrity.errors import NoEvidenceError
from taksitlio.answer_integrity.truth_status import (
    FieldTruthStatus,
    FinanceAvailability,
    is_claimable,
)


class FactType(str, Enum):
    PRICE = "PRICE"
    STOCK = "STOCK"
    INSTITUTION = "INSTITUTION"
    MERCHANT = "MERCHANT"
    CAMPAIGN = "CAMPAIGN"
    RATE = "RATE"
    MONTHLY_PAYMENT = "MONTHLY_PAYMENT"
    TOTAL_REPAYMENT = "TOTAL_REPAYMENT"
    TERM = "TERM"
    PRODUCT_ATTRIBUTE = "PRODUCT_ATTRIBUTE"
    RANKING_LABEL = "RANKING_LABEL"
    COST_KIND = "COST_KIND"
    FEES_TOTAL = "FEES_TOTAL"
    DISPLAY_NAME = "DISPLAY_NAME"


# Critical financial / product claim types that require evidence IDs.
CRITICAL_FACT_TYPES: frozenset[FactType] = frozenset(
    {
        FactType.PRICE,
        FactType.STOCK,
        FactType.INSTITUTION,
        FactType.CAMPAIGN,
        FactType.RATE,
        FactType.MONTHLY_PAYMENT,
        FactType.TOTAL_REPAYMENT,
        FactType.TERM,
        FactType.PRODUCT_ATTRIBUTE,
    }
)

EVIDENCE_FIELD_BY_TYPE: dict[FactType, str] = {
    FactType.PRICE: "price_snapshot_id",
    FactType.STOCK: "stock_snapshot_id",
    FactType.INSTITUTION: "merchant_finance_agreement_id",
    FactType.CAMPAIGN: "campaign_version_id",
    FactType.RATE: "rate_snapshot_id",
    FactType.MONTHLY_PAYMENT: "payment_calculation_id",
    FactType.TOTAL_REPAYMENT: "payment_calculation_id",
    FactType.TERM: "rate_snapshot_id",
    FactType.PRODUCT_ATTRIBUTE: "product_attribute_source_id",
}


@dataclass(frozen=True)
class EvidenceRef:
    """Provenance pointers; at least one required for critical facts."""

    price_snapshot_id: Optional[str] = None
    stock_snapshot_id: Optional[str] = None
    merchant_finance_agreement_id: Optional[str] = None
    campaign_version_id: Optional[str] = None
    rate_snapshot_id: Optional[str] = None
    payment_calculation_id: Optional[str] = None
    product_attribute_source_id: Optional[str] = None

    def for_type(self, fact_type: FactType) -> Optional[str]:
        key = EVIDENCE_FIELD_BY_TYPE.get(fact_type)
        if key is None:
            return None
        return getattr(self, key, None)

    def has_evidence(self, fact_type: FactType) -> bool:
        if fact_type not in CRITICAL_FACT_TYPES:
            return True
        return bool(self.for_type(fact_type))


@dataclass(frozen=True)
class Fact:
    fact_id: str
    fact_type: FactType
    value: str
    truth_status: FieldTruthStatus
    evidence: EvidenceRef = field(default_factory=EvidenceRef)
    display_safe: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def assert_claimable(self) -> None:
        if self.fact_type in CRITICAL_FACT_TYPES and not self.evidence.has_evidence(
            self.fact_type
        ):
            raise NoEvidenceError(self.fact_type.value)
        if not is_claimable(self.truth_status):
            raise NoEvidenceError(
                self.fact_type.value,
                f"truth_status={self.truth_status.value} is not claimable",
            )


@dataclass(frozen=True)
class FactEnvelope:
    """Verified facts eligible for response composition / LLM allowed_facts."""

    facts: tuple[Fact, ...]
    finance_availability: FinanceAvailability = FinanceAvailability.UNAVAILABLE
    ranking_winner_product_id: Optional[str] = None
    ranking_label: Optional[str] = None
    reason_codes: tuple[str, ...] = ()
    institution_names: tuple[str, ...] = ()
    merchant_names: tuple[str, ...] = ()
    product_ids: tuple[str, ...] = ()

    def claimable_facts(self) -> tuple[Fact, ...]:
        out: list[Fact] = []
        for fact in self.facts:
            if not fact.display_safe:
                continue
            if fact.fact_type in CRITICAL_FACT_TYPES:
                if not fact.evidence.has_evidence(fact.fact_type):
                    continue
                if not is_claimable(fact.truth_status):
                    continue
            out.append(fact)
        return tuple(out)

    def allowed_facts(self) -> list[dict[str, str]]:
        return [
            {
                "fact_id": f.fact_id,
                "type": f.fact_type.value,
                "value": f.value,
            }
            for f in self.claimable_facts()
        ]

    def values_for(self, fact_type: FactType) -> frozenset[str]:
        return frozenset(
            f.value for f in self.claimable_facts() if f.fact_type is fact_type
        )

    def fact_ids(self) -> tuple[str, ...]:
        return tuple(f.fact_id for f in self.claimable_facts())


def validate_provenance(facts: Sequence[Fact]) -> None:
    """Raise NoEvidenceError if any critical fact lacks evidence."""

    for fact in facts:
        if fact.fact_type not in CRITICAL_FACT_TYPES:
            continue
        if not fact.display_safe:
            continue
        if not fact.evidence.has_evidence(fact.fact_type):
            raise NoEvidenceError(fact.fact_type.value)
        if fact.truth_status is FieldTruthStatus.CONFLICTED:
            raise NoEvidenceError(
                fact.fact_type.value,
                "CONFLICTED facts cannot be claimed",
            )


def build_fact_envelope(
    facts: Sequence[Fact],
    *,
    finance_availability: FinanceAvailability = FinanceAvailability.UNAVAILABLE,
    ranking_winner_product_id: Optional[str] = None,
    ranking_label: Optional[str] = None,
    reason_codes: Sequence[str] = (),
    institution_names: Sequence[str] = (),
    merchant_names: Sequence[str] = (),
    product_ids: Sequence[str] = (),
    enforce_provenance: bool = True,
) -> FactEnvelope:
    claimable = []
    for fact in facts:
        if fact.fact_type in CRITICAL_FACT_TYPES and fact.display_safe:
            if not fact.evidence.has_evidence(fact.fact_type):
                if enforce_provenance:
                    # Drop rather than claim — no evidence → no claim.
                    continue
            elif not is_claimable(fact.truth_status):
                continue
            else:
                claimable.append(fact)
        else:
            claimable.append(fact)
    return FactEnvelope(
        facts=tuple(claimable),
        finance_availability=finance_availability,
        ranking_winner_product_id=ranking_winner_product_id,
        ranking_label=ranking_label,
        reason_codes=tuple(reason_codes),
        institution_names=tuple(institution_names),
        merchant_names=tuple(merchant_names),
        product_ids=tuple(product_ids),
    )


__all__ = [
    "CRITICAL_FACT_TYPES",
    "EVIDENCE_FIELD_BY_TYPE",
    "EVIDENCE_KEYS",
    "EvidenceRef",
    "Fact",
    "FactEnvelope",
    "FactType",
    "GroundedFact",
    "ProvenanceError",
    "assert_claimable",
    "build_envelope",
    "build_fact",
    "build_fact_envelope",
    "validate_provenance",
]

# ADR-012 naming aliases (claim_validator / package exports)
GroundedFact = Fact
ProvenanceError = NoEvidenceError
EVIDENCE_KEYS = frozenset(EVIDENCE_FIELD_BY_TYPE.values())


def build_fact(**kwargs: Any) -> Fact:
    return Fact(**kwargs)


def build_envelope(*args: Any, **kwargs: Any) -> FactEnvelope:
    if "facts" in kwargs or args:
        return FactEnvelope(*args, **kwargs) if args else FactEnvelope(**kwargs)
    return build_fact_envelope(**kwargs)


def assert_claimable(fact: Fact) -> None:
    fact.assert_claimable()
