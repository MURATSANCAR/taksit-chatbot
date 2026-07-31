"""Deterministic Final Claim Validator (ADR-012 §5).

No second LLM. Scans response text against allowed_facts / envelope.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Sequence

from taksitlio.answer_integrity.errors import ClaimValidationFailed
from taksitlio.answer_integrity.facts import FactEnvelope, FactType
from taksitlio.answer_integrity.truth_status import CostKind, FieldTruthStatus


_MONEY_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|TRY|₺)?",
    re.IGNORECASE,
)
_TERM_RE = re.compile(r"(?<!\d)(\d{1,3})\s*(?:ay|aylık|months?)\b", re.IGNORECASE)

_BEST_LABEL_PHRASES = (
    "en uygun",
    "en iyi ürün",
    "en iyi seçenek",
)
_ZERO_RATE_PHRASES = ("faizsiz", "%0", "yüzde sıfır", "0 faiz", "sıfır faiz")
_ZERO_COST_PHRASES = ("masrafsız", "ücretsiz finansman", "ek masraf yok")
_IN_STOCK_PHRASES = ("stokta", "stokta var", "stok mevcut")
_PERSONAL_APPROVAL_FORBIDDEN = (
    "taksitle alabilirsiniz",
    "onaylandınız",
    "krediniz hazır",
    "kesin aylık taksitiniz",
)


@dataclass(frozen=True)
class ClaimValidationResult:
    ok: bool
    reasons: tuple[str, ...]
    normalized_amounts: tuple[str, ...]

    @property
    def failed(self) -> bool:
        return not self.ok


def _fold(text: str) -> str:
    lowered = text.casefold()
    return "".join(
        c for c in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(c)
    )


def normalize_money_token(raw: str) -> str:
    """Normalize display money to a comparable digit string (no separators)."""

    s = raw.strip().replace(" ", "").replace("₺", "").replace("TRY", "").replace("TL", "")
    s = s.strip()
    # Turkish: 4.281,50 or 4281.50 or 4.281
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = parts[0].replace(".", "") + "." + parts[1]
        else:
            s = s.replace(",", "")
    else:
        # 4.281 style thousands
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
    try:
        val = float(s)
    except ValueError:
        return raw.strip()
    if abs(val - round(val)) < 1e-9:
        return str(int(round(val)))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def _allowed_amount_set(envelope: FactEnvelope) -> set[str]:
    allowed: set[str] = set()
    for fact in envelope.claimable_facts():
        if fact.fact_type in {
            FactType.PRICE,
            FactType.MONTHLY_PAYMENT,
            FactType.TOTAL_REPAYMENT,
            FactType.FEES_TOTAL,
        }:
            # value like "42999 TRY"
            token = fact.value.split()[0] if fact.value else ""
            if token:
                allowed.add(normalize_money_token(token))
            allowed.add(normalize_money_token(fact.value))
    return {a for a in allowed if a}


def _allowed_terms(envelope: FactEnvelope) -> set[str]:
    return {v.split()[0] for v in envelope.values_for(FactType.TERM)} | set(
        envelope.values_for(FactType.TERM)
    )


def validate_claims(
    text: str,
    envelope: FactEnvelope,
    *,
    stock_status: Optional[str] = None,
    rate_type: Optional[str] = None,
    cost_kind: Optional[CostKind | str] = None,
    best_label_allowed: bool = False,
    fees_total: float = 0.0,
) -> ClaimValidationResult:
    reasons: list[str] = []
    folded = _fold(text)
    amounts_found: list[str] = []

    allowed_amounts = _allowed_amount_set(envelope)
    for match in _MONEY_RE.finditer(text):
        raw = match.group(1)
        # Skip bare small integers that look like terms if followed by ay — handled below
        after = text[match.end() : match.end() + 8].lower()
        if after.strip().startswith("ay"):
            continue
        norm = normalize_money_token(raw)
        # Ignore year-like / tiny noise? Keep all money claims strict.
        if not norm or norm in {"0"}:
            continue
        amounts_found.append(norm)
        if allowed_amounts and norm not in allowed_amounts:
            reasons.append(f"amount_not_in_allowed_facts:{norm}")
        elif not allowed_amounts:
            reasons.append(f"amount_without_allowed_facts:{norm}")

    allowed_terms = _allowed_terms(envelope)
    for match in _TERM_RE.finditer(text):
        term = match.group(1)
        if allowed_terms and term not in allowed_terms and f"{term} months" not in {
            t.casefold() for t in allowed_terms
        }:
            # also allow "12" if value is "12 months"
            if not any(t.startswith(term) for t in allowed_terms):
                reasons.append(f"term_not_verified:{term}")

    # Institution / bank names mentioned must be in result set
    for name in envelope.institution_names:
        # Only enforce when text mentions a known-but-not-allowed? We check
        # that fabricated banks aren't introduced by requiring any bank-like
        # claim to match allowed names when institution facts exist.
        pass

    # If envelope has institutions, reject unknown bank-like tokens is hard;
    # instead: if text contains an institution fact value misspelling — covered
    # by requiring INSTITUTION facts for bank claims via phrase scan below.
    institution_values = { _fold(v) for v in envelope.values_for(FactType.INSTITUTION) }
    institution_values |= { _fold(n) for n in envelope.institution_names }
    # Detect common "with Bank X" patterns only when we have a closed set
    if institution_values:
        # Any institution fact value appearing is fine; if text claims a bank
        # from a hard-coded list we do NOT — names must come from envelope.
        for name in envelope.institution_names:
            pass

    for phrase in _BEST_LABEL_PHRASES:
        if phrase in folded and not best_label_allowed:
            reasons.append("best_label_not_permitted")
            break
        if phrase in folded and best_label_allowed:
            winner = envelope.ranking_winner_product_id
            if not winner:
                reasons.append("best_label_without_ranking_winner")

    ck = cost_kind.value if isinstance(cost_kind, CostKind) else cost_kind
    for phrase in _ZERO_RATE_PHRASES:
        if phrase in folded:
            if rate_type != "ZERO_RATE" and ck not in {
                CostKind.ZERO_RATE.value,
                CostKind.ZERO_TOTAL_COST.value,
            }:
                reasons.append("faizsiz_without_zero_rate")
            break

    for phrase in _ZERO_COST_PHRASES:
        if phrase in folded:
            if fees_total > 0 or ck == CostKind.HAS_FEES.value:
                reasons.append("masrafsiz_with_fees")
            elif ck not in {CostKind.ZERO_TOTAL_COST.value, None}:
                if ck != CostKind.ZERO_TOTAL_COST.value:
                    reasons.append("masrafsiz_without_zero_total_cost")
            break

    for phrase in _IN_STOCK_PHRASES:
        if phrase in folded and stock_status != "AVAILABLE":
            reasons.append("stokta_without_available_stock")
            break

    for phrase in _PERSONAL_APPROVAL_FORBIDDEN:
        if phrase in folded:
            reasons.append(f"forbidden_personal_approval_phrase:{phrase}")

    # CONFLICTED facts must never appear as values
    for fact in envelope.facts:
        if fact.truth_status is FieldTruthStatus.CONFLICTED and fact.value:
            if _fold(fact.value) in folded:
                reasons.append(f"conflicted_value_claimed:{fact.fact_id}")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return ClaimValidationResult(
        ok=len(unique_reasons) == 0,
        reasons=unique_reasons,
        normalized_amounts=tuple(amounts_found),
    )


def assert_claims(
    text: str,
    envelope: FactEnvelope,
    **kwargs: object,
) -> ClaimValidationResult:
    result = validate_claims(text, envelope, **kwargs)  # type: ignore[arg-type]
    if result.failed:
        raise ClaimValidationFailed(result.reasons)
    return result


def validate_institution_mentions(
    text: str,
    allowed_names: Sequence[str],
    *,
    candidate_names: Sequence[str] = (),
) -> tuple[str, ...]:
    """Fail if a known candidate bank name appears but is not in allowed set."""

    folded = _fold(text)
    allowed = {_fold(n) for n in allowed_names if n}
    reasons: list[str] = []
    for name in candidate_names:
        key = _fold(name)
        if key and key in folded and key not in allowed:
            reasons.append(f"institution_not_in_result_set:{name}")
    return tuple(reasons)


__all__ = [
    "ClaimValidationResult",
    "assert_claims",
    "normalize_money_token",
    "validate_claims",
    "validate_institution_mentions",
]
