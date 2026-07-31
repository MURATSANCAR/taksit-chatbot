"""Deterministic Response Composer (ADR-012 §4).

Builds user text only from FactEnvelope. Optional LLM decoration is validated
separately; on failure this composer’s template is the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from taksitlio.answer_integrity.facts import Fact, FactEnvelope, FactType
from taksitlio.answer_integrity.truth_status import (
    FinanceAvailability,
    ResponseOutcome,
)


APPROVAL_DISCLAIMER = (
    "Nihai limit ve onay finans kuruluşunun değerlendirmesine bağlıdır."
)

CANNOT_VERIFY_TEMPLATE = (
    "{topic} ilişkin güncel doğrulanmış kayıt bulunamadı. "
    "Mevcut doğrulanmış seçenekleri gösterebilirim."
)


@dataclass(frozen=True)
class ComposedResponse:
    text: str
    outcome: ResponseOutcome
    template_used: str
    fact_ids: tuple[str, ...]
    allowed_facts: tuple[dict[str, str], ...]
    reason_explanation: Optional[str] = None
    used_llm: bool = False
    grounded: bool = True
    used_model: bool = False
    claim_reasons: tuple[str, ...] = ()
    claim_validation: Optional[object] = None


def _fact_value(envelope: FactEnvelope, fact_type: FactType) -> Optional[str]:
    for fact in envelope.claimable_facts():
        if fact.fact_type is fact_type:
            return fact.value
    return None


def _missing_critical(envelope: FactEnvelope) -> list[str]:
    missing: list[str] = []
    have = {f.fact_type for f in envelope.claimable_facts()}
    # Soft: PRICE is critical for product answers when product_ids present
    if envelope.product_ids and FactType.PRICE not in have:
        missing.append("price")
    return missing


def compose_reason_explanation(reason_codes: Sequence[str]) -> str:
    """Deterministic 'neden?' text from reason codes — never LLM-invented."""

    mapping = {
        "REQUIRED_ATTRIBUTES_MATCHED": "zorunlu özellik koşullarını karşılıyor",
        "WITHIN_BUDGET": "bütçenizin altında",
        "LOWEST_TOTAL_REPAYMENT": "toplam geri ödemesi diğer doğrulanmış adaylardan daha düşük",
        "LOWEST_MONTHLY_PAYMENT": "aylık ödemesi diğer doğrulanmış adaylardan daha düşük",
        "LOWEST_PRODUCT_PRICE": "satış fiyatı diğer doğrulanmış adaylardan daha düşük",
        "STOCK_VERIFIED": "stok bilgisi güncel ve doğrulanmış",
        "FRESH_PRICE": "fiyat bilgisi taze",
        "FINANCE_MAPPING_VERIFIED": "banka/kampanya eşlemesi doğrulanmış",
        "CAMPAIGN_ACTIVE": "kampanya aktif",
    }
    parts = [mapping[c] for c in reason_codes if c in mapping]
    if not parts:
        return "Bu sıralama doğrulanmış kriterlere göre üretildi."
    if len(parts) == 1:
        return f"Bu ürünü önerdim çünkü {parts[0]}."
    head, tail = parts[:-1], parts[-1]
    return "Bu ürünü ilk sıraya aldım çünkü " + ", ".join(head) + f" ve {tail}."


def compose_deterministic(
    envelope: FactEnvelope,
    *,
    need_description: str = "ihtiyacınız",
    cards: Sequence[Mapping[str, object]] = (),
    clarifications: Sequence[str] = (),
) -> ComposedResponse:
    allowed = tuple(envelope.allowed_facts())
    fact_ids = envelope.fact_ids()

    if clarifications and not cards and not envelope.claimable_facts():
        return ComposedResponse(
            text=" ".join(clarifications),
            outcome=ResponseOutcome.PARTIALLY_ANSWERED,
            template_used="clarify",
            fact_ids=fact_ids,
            allowed_facts=allowed,
        )

    if not envelope.claimable_facts() and not cards:
        return ComposedResponse(
            text=CANNOT_VERIFY_TEMPLATE.format(topic="Bu sorguya"),
            outcome=ResponseOutcome.CANNOT_VERIFY,
            template_used="cannot_verify",
            fact_ids=(),
            allowed_facts=(),
        )

    lines: list[str] = [f"{need_description} için doğrulanmış seçenekler:"]
    if cards:
        for i, card in enumerate(cards, start=1):
            name = str(card.get("display_name") or card.get("name") or f"Ürün {i}")
            price = card.get("price")
            currency = card.get("currency") or "TRY"
            price_part = ""
            if price is not None and _fact_value(envelope, FactType.PRICE):
                price_part = f" — {float(price):,.0f} {currency}".replace(",", ".")
            elif price is not None:
                # Card price only if matching claimable price fact exists for product
                pid = str(card.get("product_id") or "")
                price_facts = [
                    f
                    for f in envelope.claimable_facts()
                    if f.fact_type is FactType.PRICE
                    and (
                        not pid
                        or f.metadata.get("product_id") in (None, pid)
                    )
                ]
                if price_facts:
                    price_part = f" — {float(price):,.0f} {currency}".replace(",", ".")
            merchant = ""
            m = card.get("merchant")
            if isinstance(m, Mapping):
                merchant = str(m.get("display_name") or "")
            merchant_part = f" ({merchant})" if merchant else ""
            lines.append(f"{i}. {name}{merchant_part}{price_part}")
    else:
        # Fact-only summary
        price = _fact_value(envelope, FactType.PRICE)
        monthly = _fact_value(envelope, FactType.MONTHLY_PAYMENT)
        term = _fact_value(envelope, FactType.TERM)
        inst = _fact_value(envelope, FactType.INSTITUTION)
        name = _fact_value(envelope, FactType.DISPLAY_NAME) or "Ürün"
        line = f"1. {name}"
        if price:
            line += f" — {price}"
        lines.append(line)
        if monthly and term:
            detail = f"   Tahmini aylık ödeme: {monthly} / {term}"
            if inst:
                detail += f" — {inst}"
            lines.append(detail)

    if envelope.finance_availability is FinanceAvailability.AVAILABLE:
        lines.append(
            "Bu ürün için finansman seçeneği mevcut görünüyor. " + APPROVAL_DISCLAIMER
        )
    elif envelope.finance_availability is FinanceAvailability.RULE_ELIGIBLE:
        lines.append(
            "Genel kurallar açısından uygun görünüyor. " + APPROVAL_DISCLAIMER
        )

    if envelope.ranking_label:
        lines.append(f"Sıralama etiketi: {envelope.ranking_label}")

    reason_text = None
    if envelope.reason_codes:
        reason_text = compose_reason_explanation(envelope.reason_codes)

    missing = _missing_critical(envelope)
    if missing and envelope.claimable_facts():
        outcome = ResponseOutcome.PARTIALLY_ANSWERED
    elif envelope.claimable_facts() or cards:
        outcome = ResponseOutcome.ANSWERED
    else:
        outcome = ResponseOutcome.CANNOT_VERIFY

    return ComposedResponse(
        text="\n".join(lines),
        outcome=outcome,
        template_used="deterministic_facts",
        fact_ids=fact_ids,
        allowed_facts=allowed,
        reason_explanation=reason_text,
    )


def llm_allowed_fields() -> frozenset[str]:
    return frozenset({"summary", "comparison_explanation", "clarification_question"})


def filter_llm_decoration(payload: Mapping[str, object]) -> dict[str, str]:
    """LLM may only emit summary / comparison / clarification — no new facts."""

    out: dict[str, str] = {}
    for key in llm_allowed_fields():
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def merge_decoration(
    base: ComposedResponse,
    decoration: Mapping[str, str],
) -> str:
    parts = [base.text]
    if decoration.get("summary"):
        parts.append(decoration["summary"])
    if decoration.get("comparison_explanation"):
        parts.append(decoration["comparison_explanation"])
    if decoration.get("clarification_question"):
        parts.append(decoration["clarification_question"])
    if base.reason_explanation:
        parts.append(base.reason_explanation)
    return "\n\n".join(parts)


def compose_from_facts(
    envelope: FactEnvelope,
    *,
    need_description: str = "ihtiyacınız",
    eligibility: object = None,
    reason_explanations: Sequence[str] = (),
    cards: Sequence[Mapping[str, object]] = (),
    clarifications: Sequence[str] = (),
) -> ComposedResponse:
    """Alias for compose_deterministic (pipeline / ADR-012 test API)."""

    _ = eligibility
    base = compose_deterministic(
        envelope,
        need_description=need_description,
        cards=cards,
        clarifications=clarifications,
    )
    if reason_explanations and not base.reason_explanation:
        reasons = tuple(str(r) for r in reason_explanations if r)
        return ComposedResponse(
            text=base.text,
            outcome=base.outcome,
            template_used=base.template_used,
            fact_ids=base.fact_ids,
            allowed_facts=base.allowed_facts,
            reason_explanation=" ".join(reasons),
            claim_reasons=reasons,
        )
    return base


def merge_optional_llm_fields(
    base: ComposedResponse,
    llm_fields: Mapping[str, str],
    *,
    envelope: Optional[FactEnvelope] = None,
    ranking_winner_ids: Sequence[str] = (),
    result_institution_names: Sequence[str] = (),
) -> ComposedResponse:
    """Merge LLM decoration; on claim failure fall back to deterministic template."""

    from taksitlio.answer_integrity.claim_validator import validate_claims

    decoration = filter_llm_decoration(llm_fields)
    if not decoration:
        return base
    merged_text = merge_decoration(base, decoration)
    env = envelope or FactEnvelope(facts=())
    cv = validate_claims(
        merged_text,
        env,
        ranking_winner_ids=ranking_winner_ids,
        result_institution_names=result_institution_names,
    )
    if cv.failed:
        return ComposedResponse(
            text=base.text,
            outcome=ResponseOutcome.CLAIM_VALIDATION_FAILED,
            template_used="llm_rejected_template_fallback",
            fact_ids=base.fact_ids,
            allowed_facts=base.allowed_facts,
            reason_explanation=base.reason_explanation,
            used_llm=False,
            grounded=True,
            claim_validation=cv,
        )
    return ComposedResponse(
        text=merged_text,
        outcome=base.outcome,
        template_used="llm_decorated",
        fact_ids=base.fact_ids,
        allowed_facts=base.allowed_facts,
        reason_explanation=base.reason_explanation,
        used_llm=True,
        used_model=True,
        grounded=True,
        claim_validation=cv,
    )


__all__ = [
    "APPROVAL_DISCLAIMER",
    "CANNOT_VERIFY_TEMPLATE",
    "ComposedResponse",
    "compose_deterministic",
    "compose_from_facts",
    "compose_reason_explanation",
    "filter_llm_decoration",
    "llm_allowed_fields",
    "merge_decoration",
    "merge_optional_llm_fields",
]
