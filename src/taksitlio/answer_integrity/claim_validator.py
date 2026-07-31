"""Final Claim Validator — deterministic, no second LLM (ADR-012 §5)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from taksitlio.answer_integrity.eligibility import contains_forbidden_approval_claim
from taksitlio.answer_integrity.facts import FactEnvelope, FactType, GroundedFact


CLAIM_VALIDATION_FAILED = "CLAIM_VALIDATION_FAILED"

# Turkish / common money patterns: 4.281, 4281, 4.281,50, 42999 TRY
_MONEY_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{1,2})?|\d+)(?:\s*(?:TL|TRY|₺))?",
    re.IGNORECASE,
)
_TERM_RE = re.compile(r"(\d+)\s*(?:ay|aylık|months?)", re.IGNORECASE)

_BEST_LABEL_RE = re.compile(
    r"\b(en uygun|en iyi|en ucuz|lowest|best overall)\b",
    re.IGNORECASE,
)
_ZERO_RATE_RE = re.compile(
    r"\b(faizsiz|%0|yüzde\s*sıfır|zero[\s-]?rate|masrafsız)\b",
    re.IGNORECASE,
)
_IN_STOCK_RE = re.compile(r"\b(stokta|stokta var|available|in stock)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ClaimViolation:
    code: str
    detail: str
    excerpt: str = ""


@dataclass(frozen=True)
class ClaimValidationResult:
    ok: bool
    outcome: str  # OK | CLAIM_VALIDATION_FAILED
    violations: tuple[ClaimViolation, ...] = ()
    used_fact_ids: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return not self.ok

    @property
    def reasons(self) -> tuple[str, ...]:
        """Backward-compatible alias used by grounded response paths."""

        return tuple(v.code for v in self.violations)


def _normalize_money(token: str) -> Optional[str]:
    t = token.strip().replace(" ", "").replace("₺", "")
    t = re.sub(r"(?i)try|tl$", "", t).strip()
    if not t:
        return None
    # 4.281,50 (TR) or 4,281.50 (EN)
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        parts = t.split(",")
        if len(parts[-1]) <= 2:
            t = t.replace(",", ".")
        else:
            t = t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        val = float(t)
    except ValueError:
        return None
    if val != val:  # NaN
        return None
    if abs(val - round(val)) < 1e-9:
        return str(int(round(val)))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def _allowed_money_set(facts: Sequence[GroundedFact]) -> set[str]:
    out: set[str] = set()
    money_types = {
        FactType.PRICE,
        FactType.MONTHLY_PAYMENT,
        FactType.TOTAL_REPAYMENT,
        FactType.FEES,
        FactType.FEES_TOTAL,
    }
    for f in facts:
        if f.fact_type not in money_types:
            continue
        raw = f.value
        for m in _MONEY_RE.finditer(raw):
            norm = _normalize_money(m.group(1))
            if norm:
                out.add(norm)
        norm_whole = _normalize_money(raw.split()[0] if raw.split() else raw)
        if norm_whole:
            out.add(norm_whole)
    return out


def _allowed_institutions(facts: Sequence[GroundedFact]) -> set[str]:
    names: set[str] = set()
    for f in facts:
        if f.fact_type is FactType.INSTITUTION:
            names.add(f.value.casefold())
        inst = f.metadata.get("institution_display_name")
        if inst:
            names.add(str(inst).casefold())
    return names


def _allowed_terms(facts: Sequence[GroundedFact]) -> set[int]:
    terms: set[int] = set()
    for f in facts:
        if f.fact_type is FactType.TERM:
            try:
                terms.add(int(re.sub(r"\D", "", f.value) or "0"))
            except ValueError:
                continue
        term = f.metadata.get("term_months")
        if term is not None:
            try:
                terms.add(int(term))
            except (TypeError, ValueError):
                continue
    return {t for t in terms if t > 0}


def validate_claims(
    text: str,
    envelope: FactEnvelope,
    *,
    ranking_winner_ids: Optional[Sequence[str]] = None,
    result_institution_names: Optional[Sequence[str]] = None,
    stock_status: Optional[str] = None,
    rate_type: Optional[str] = None,
    cost_kind: Optional[str] = None,
    best_label_allowed: bool = False,
    fees_total: float = 0.0,
) -> ClaimValidationResult:
    """Deterministic claim grounding. Failure → caller must use template."""

    _ = stock_status, rate_type, cost_kind, best_label_allowed, fees_total
    claimable = envelope.claimable()
    violations: list[ClaimViolation] = []
    used: list[str] = []

    if contains_forbidden_approval_claim(text):
        violations.append(
            ClaimViolation(
                code="FORBIDDEN_PERSONAL_APPROVAL_CLAIM",
                detail="text asserts personal installment approval",
            )
        )

    allowed_money = _allowed_money_set(claimable)
    for m in _MONEY_RE.finditer(text):
        span_start = m.start()
        window = text[span_start : m.end() + 8].casefold()
        if re.search(r"^\d+\s*(?:ay|aylık|months?)", window):
            continue
        norm = _normalize_money(m.group(1))
        if norm is None:
            continue
        try:
            as_float = float(norm)
        except ValueError:
            continue
        if as_float < 10 and norm not in allowed_money:
            continue
        if norm not in allowed_money:
            violations.append(
                ClaimViolation(
                    code="UNGROUNDED_MONEY",
                    detail=f"amount {norm} not in allowed_facts",
                    excerpt=m.group(0),
                )
            )
        else:
            for f in claimable:
                if norm in _allowed_money_set([f]):
                    used.append(f.fact_id)

    inst_allowed = set(_allowed_institutions(claimable))
    if result_institution_names:
        inst_allowed |= {n.casefold() for n in result_institution_names}
    if result_institution_names is not None:
        for name in result_institution_names:
            if name.casefold() in text.casefold():
                if name.casefold() not in inst_allowed and name.casefold() not in {
                    f.value.casefold()
                    for f in claimable
                    if f.fact_type is FactType.INSTITUTION
                }:
                    violations.append(
                        ClaimViolation(
                            code="UNGROUNDED_INSTITUTION",
                            detail=f"institution {name} not in result set",
                            excerpt=name,
                        )
                    )

    allowed_terms = _allowed_terms(claimable)
    for m in _TERM_RE.finditer(text):
        term = int(m.group(1))
        if allowed_terms and term not in allowed_terms:
            violations.append(
                ClaimViolation(
                    code="UNGROUNDED_TERM",
                    detail=f"term {term} not verified",
                    excerpt=m.group(0),
                )
            )
        elif not allowed_terms and term > 0:
            violations.append(
                ClaimViolation(
                    code="UNGROUNDED_TERM",
                    detail=f"term {term} not verified",
                    excerpt=m.group(0),
                )
            )

    if _BEST_LABEL_RE.search(text):
        winners = set(ranking_winner_ids or [])
        winner_facts = [
            f for f in claimable if f.fact_type is FactType.RANKING_WINNER
        ]
        if winner_facts:
            winners |= {f.value for f in winner_facts}
            winners |= {
                str(f.metadata.get("product_id"))
                for f in winner_facts
                if f.metadata.get("product_id")
            }
        if not winners:
            violations.append(
                ClaimViolation(
                    code="UNGROUNDED_BEST_LABEL",
                    detail="'en uygun' used without ranking winner",
                )
            )

    if _ZERO_RATE_RE.search(text):
        rate_facts = [f for f in claimable if f.fact_type is FactType.RATE_TYPE]
        zero_ok = any(
            f.value.upper() in {"ZERO_RATE", "ZERO_TOTAL_COST"} for f in rate_facts
        )
        if re.search(r"\bmasrafsız\b", text, re.IGNORECASE):
            if not any(f.value.upper() == "ZERO_TOTAL_COST" for f in rate_facts):
                violations.append(
                    ClaimViolation(
                        code="INVALID_ZERO_COST_CLAIM",
                        detail="masrafsız requires ZERO_TOTAL_COST",
                    )
                )
        elif not zero_ok:
            violations.append(
                ClaimViolation(
                    code="INVALID_ZERO_RATE_CLAIM",
                    detail="faizsiz/%0 requires rate_type ZERO_RATE",
                )
            )

    if _IN_STOCK_RE.search(text):
        stock_facts = [f for f in claimable if f.fact_type is FactType.STOCK]
        if not any(f.value.upper() == "AVAILABLE" for f in stock_facts):
            violations.append(
                ClaimViolation(
                    code="UNGROUNDED_STOCK_CLAIM",
                    detail="'stokta' requires STOCK AVAILABLE fact",
                )
            )

    ok = not violations
    return ClaimValidationResult(
        ok=ok,
        outcome="OK" if ok else CLAIM_VALIDATION_FAILED,
        violations=tuple(violations),
        used_fact_ids=tuple(dict.fromkeys(used)),
    )


@dataclass
class ClaimValidator:
    """Callable wrapper for pipeline wiring."""

    ranking_winner_ids: tuple[str, ...] = ()
    result_institution_names: tuple[str, ...] = ()

    def validate(self, text: str, envelope: FactEnvelope) -> ClaimValidationResult:
        return validate_claims(
            text,
            envelope,
            ranking_winner_ids=self.ranking_winner_ids,
            result_institution_names=self.result_institution_names,
        )


__all__ = [
    "CLAIM_VALIDATION_FAILED",
    "ClaimValidationResult",
    "ClaimValidator",
    "ClaimViolation",
    "validate_claims",
]
