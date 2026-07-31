"""Card field truth envelope helper (ADR-012 §3)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from taksitlio.answer_integrity.facts import FactType, GroundedFact, build_envelope, build_fact
from taksitlio.answer_integrity.truth_status import FieldTruthStatus


def truth_status_for_freshness(freshness: str) -> FieldTruthStatus:
    f = (freshness or "").upper()
    if f == "FRESH":
        return FieldTruthStatus.VERIFIED
    if f == "STALE":
        return FieldTruthStatus.STALE
    if f in {"EXPIRED", "SOURCE_UNAVAILABLE", "UNVERIFIED"}:
        return FieldTruthStatus.UNAVAILABLE
    return FieldTruthStatus.UNAVAILABLE


def facts_from_product_card(
    card: Mapping[str, Any],
    *,
    price_snapshot_id: Optional[str] = None,
    stock_snapshot_id: Optional[str] = None,
    payment_calculation_id: Optional[str] = None,
    campaign_version_id: Optional[str] = None,
    rate_snapshot_id: Optional[str] = None,
    price_freshness: str = "FRESH",
    stock_status: Optional[str] = None,
) -> tuple[GroundedFact, ...]:
    pid = str(card.get("product_id") or "unknown")
    facts: list[GroundedFact] = []
    facts.append(
        build_fact(
            fact_id=f"product_{pid}",
            fact_type=FactType.PRODUCT,
            value=str(card.get("display_name") or ""),
            truth_status=FieldTruthStatus.VERIFIED,
            metadata={"product_id": pid},
        )
    )
    if card.get("price") is not None:
        status = truth_status_for_freshness(price_freshness)
        evidence = {}
        if price_snapshot_id:
            evidence["price_snapshot_id"] = price_snapshot_id
        facts.append(
            build_fact(
                fact_id=f"price_{pid}",
                fact_type=FactType.PRICE,
                value=f"{card['price']} {card.get('currency') or 'TRY'}",
                truth_status=status if evidence else FieldTruthStatus.UNAVAILABLE,
                evidence=evidence,
                checked_at=card.get("price_checked_at"),
                metadata={"product_id": pid},
            )
        )
    stock = stock_status or card.get("stock_status")
    if stock:
        evidence = {}
        if stock_snapshot_id:
            evidence["stock_snapshot_id"] = stock_snapshot_id
        facts.append(
            build_fact(
                fact_id=f"stock_{pid}",
                fact_type=FactType.STOCK,
                value=str(stock),
                truth_status=FieldTruthStatus.VERIFIED if evidence else FieldTruthStatus.UNAVAILABLE,
                evidence=evidence,
                metadata={"product_id": pid},
            )
        )
    finance = card.get("best_finance")
    if isinstance(finance, Mapping):
        if finance.get("monthly_payment") is not None:
            evidence = {}
            if payment_calculation_id:
                evidence["payment_calculation_id"] = payment_calculation_id
            facts.append(
                build_fact(
                    fact_id=f"pay_{pid}",
                    fact_type=FactType.MONTHLY_PAYMENT,
                    value=f"{finance['monthly_payment']} {card.get('currency') or 'TRY'}",
                    truth_status=(
                        FieldTruthStatus.CALCULATED_ESTIMATE
                        if evidence
                        else FieldTruthStatus.UNAVAILABLE
                    ),
                    evidence=evidence,
                    display_label=str(finance.get("display_label") or "Tahmini aylık ödeme"),
                    metadata={
                        "product_id": pid,
                        "term_months": finance.get("term_months"),
                        "institution_display_name": finance.get("institution_display_name"),
                    },
                )
            )
        if finance.get("term_months") is not None:
            evidence = {}
            if campaign_version_id:
                evidence["campaign_version_id"] = campaign_version_id
            facts.append(
                build_fact(
                    fact_id=f"term_{pid}",
                    fact_type=FactType.TERM,
                    value=f"{finance['term_months']} ay",
                    truth_status=FieldTruthStatus.VERIFIED if evidence else FieldTruthStatus.UNAVAILABLE,
                    evidence=evidence,
                    metadata={"term_months": int(finance["term_months"])},
                )
            )
        inst = finance.get("institution_display_name")
        if inst:
            facts.append(
                build_fact(
                    fact_id=f"inst_{pid}",
                    fact_type=FactType.INSTITUTION,
                    value=str(inst),
                    truth_status=FieldTruthStatus.VERIFIED,
                )
            )
        if rate_snapshot_id:
            facts.append(
                build_fact(
                    fact_id=f"rate_{pid}",
                    fact_type=FactType.RATE,
                    value=rate_snapshot_id,
                    truth_status=FieldTruthStatus.VERIFIED,
                    evidence={"rate_snapshot_id": rate_snapshot_id},
                )
            )
    return tuple(facts)


def envelope_from_cards(
    cards: list[Mapping[str, Any]],
    *,
    evidence_by_product: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> Any:
    evidence_by_product = evidence_by_product or {}
    all_facts: list[GroundedFact] = []
    for card in cards:
        pid = str(card.get("product_id") or "")
        ev = evidence_by_product.get(pid, {})
        all_facts.extend(
            facts_from_product_card(
                card,
                price_snapshot_id=ev.get("price_snapshot_id"),
                stock_snapshot_id=ev.get("stock_snapshot_id"),
                payment_calculation_id=ev.get("payment_calculation_id"),
                campaign_version_id=ev.get("campaign_version_id"),
                rate_snapshot_id=ev.get("rate_snapshot_id"),
                price_freshness=str(ev.get("price_freshness") or "FRESH"),
            )
        )
    return build_envelope(all_facts)


__all__ = [
    "envelope_from_cards",
    "facts_from_product_card",
    "truth_status_for_freshness",
]
