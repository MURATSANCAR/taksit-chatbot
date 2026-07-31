"""Eligibility vs availability vs personal approval (ADR-012 §8)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FinanceAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class RuleEligibility(str, Enum):
    RULE_ELIGIBLE = "RULE_ELIGIBLE"
    RULE_INELIGIBLE = "RULE_INELIGIBLE"
    UNKNOWN = "UNKNOWN"


class PersonalApproval(str, Enum):
    PERSONAL_APPROVAL_REQUIRED = "PERSONAL_APPROVAL_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


FORBIDDEN_CERTAIN_PHRASES = (
    "taksitle alabilirsiniz",
    "kesin onaylandı",
    "limitiniz yeterli",
    "kredi onaylandı",
)


ALLOWED_DISCLAIMER = (
    "Bu ürün için finansman seçeneği mevcut görünüyor. "
    "Nihai limit ve onay finans kuruluşunun değerlendirmesine bağlıdır."
)


@dataclass(frozen=True)
class FinanceEligibilityState:
    availability: FinanceAvailability
    rule_eligibility: RuleEligibility
    personal_approval: PersonalApproval = PersonalApproval.PERSONAL_APPROVAL_REQUIRED

    def user_safe_message(self, *, term_months: int | None = None) -> str:
        if self.availability is not FinanceAvailability.AVAILABLE:
            return "Bu ürün için doğrulanmış finansman seçeneği bulunamadı."
        if self.rule_eligibility is RuleEligibility.RULE_INELIGIBLE:
            return "Bu ürün genel finansman kurallarını karşılamıyor."
        term_part = f" {term_months} ay" if term_months else ""
        return (
            f"Bu ürün için{term_part} finansman seçeneği mevcut görünüyor. "
            "Nihai limit ve onay finans kuruluşunun değerlendirmesine bağlıdır."
        )


def contains_forbidden_approval_claim(text: str) -> bool:
    lowered = text.casefold()
    return any(p in lowered for p in FORBIDDEN_CERTAIN_PHRASES)


__all__ = [
    "ALLOWED_DISCLAIMER",
    "FORBIDDEN_CERTAIN_PHRASES",
    "FinanceAvailability",
    "FinanceEligibilityState",
    "PersonalApproval",
    "RuleEligibility",
    "contains_forbidden_approval_claim",
]
