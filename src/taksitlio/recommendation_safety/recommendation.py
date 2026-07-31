"""Recommendation integrity gate (ADR-012 §13)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from taksitlio.answer_integrity.truth_status import FieldTruthStatus, UNSAFE_FOR_BEST_OFFER
from taksitlio.product_query.ranking import RankableProduct, RankingMode, rank_products
from taksitlio.recommendation_safety.reason_codes import explain_reason_codes


LABEL_BEST = "En uygun"
LABEL_NEAREST = "Kriterlerinize en yakın seçenek"
LABEL_LOWEST_PRICE = "En düşük satış fiyatı"
LABEL_LOWEST_MONTHLY = "En düşük aylık ödeme"
LABEL_LOWEST_TOTAL = "En düşük toplam geri ödeme"


@dataclass(frozen=True)
class IntegritySignals:
    comparable_candidate_count: int
    prices_fresh: bool
    stock_verified: bool
    variants_comparable: bool
    total_repayment_present: bool
    bank_mapping_verified: bool
    campaign_active: bool
    critical_attributes_complete: bool
    field_statuses: tuple[FieldTruthStatus, ...] = ()
    is_sponsored: bool = False


@dataclass(frozen=True)
class IntegrityDecision:
    allow_best_label: bool
    label: str
    missing: tuple[str, ...]
    reason_codes: tuple[str, ...]


def evaluate_recommendation_integrity(signals: IntegritySignals) -> IntegrityDecision:
    missing: list[str] = []
    if signals.comparable_candidate_count < 3:
        missing.append("min_comparable_candidates")
    if not signals.prices_fresh:
        missing.append("fresh_prices")
    if not signals.stock_verified:
        missing.append("stock_verified")
    if not signals.variants_comparable:
        missing.append("variants_comparable")
    if not signals.total_repayment_present:
        missing.append("total_repayment")
    if not signals.bank_mapping_verified:
        missing.append("bank_mapping")
    if not signals.campaign_active:
        missing.append("campaign_active")
    if not signals.critical_attributes_complete:
        missing.append("critical_attributes")
    if signals.is_sponsored:
        missing.append("sponsored_cannot_be_best")
    if any(s in UNSAFE_FOR_BEST_OFFER for s in signals.field_statuses):
        missing.append("unsafe_field_status")

    allow = not missing
    reason_codes: list[str] = []
    if signals.critical_attributes_complete:
        reason_codes.append("REQUIRED_ATTRIBUTES_MATCHED")
    if signals.prices_fresh:
        reason_codes.append("FRESH_PRICE")
    if signals.stock_verified:
        reason_codes.append("STOCK_VERIFIED")
    if signals.total_repayment_present:
        reason_codes.append("LOWEST_TOTAL_REPAYMENT")
    if signals.bank_mapping_verified:
        reason_codes.append("FINANCE_MAPPING_VERIFIED")
    if signals.campaign_active:
        reason_codes.append("CAMPAIGN_ACTIVE")
    if signals.variants_comparable:
        reason_codes.append("VARIANT_COMPARABLE")

    return IntegrityDecision(
        allow_best_label=allow,
        label=LABEL_BEST if allow else LABEL_NEAREST,
        missing=tuple(missing),
        reason_codes=tuple(reason_codes),
    )


@dataclass(frozen=True)
class TripleWinnerSet:
    lowest_price_id: Optional[str]
    lowest_monthly_id: Optional[str]
    lowest_total_id: Optional[str]

    def labels_for(self, product_id: str) -> tuple[str, ...]:
        labels: list[str] = []
        if product_id == self.lowest_price_id:
            labels.append(LABEL_LOWEST_PRICE)
        if product_id == self.lowest_monthly_id:
            labels.append(LABEL_LOWEST_MONTHLY)
        if product_id == self.lowest_total_id:
            labels.append(LABEL_LOWEST_TOTAL)
        return tuple(labels)


def compute_triple_winners(items: Sequence[RankableProduct]) -> TripleWinnerSet:
    price = rank_products(items, mode=RankingMode.CHEAPEST_PRODUCT_PRICE)
    monthly = rank_products(items, mode=RankingMode.LOWEST_MONTHLY_PAYMENT)
    total = rank_products(items, mode=RankingMode.LOWEST_TOTAL_REPAYMENT)

    def _top(ranked) -> Optional[str]:
        for r in ranked:
            if not r.disqualified:
                return r.product_id
        return None

    return TripleWinnerSet(
        lowest_price_id=_top(price),
        lowest_monthly_id=_top(monthly),
        lowest_total_id=_top(total),
    )


def why_recommended(reason_codes: Sequence[str]) -> str:
    return explain_reason_codes(reason_codes)


__all__ = [
    "LABEL_BEST",
    "LABEL_LOWEST_MONTHLY",
    "LABEL_LOWEST_PRICE",
    "LABEL_LOWEST_TOTAL",
    "LABEL_NEAREST",
    "IntegrityDecision",
    "IntegritySignals",
    "TripleWinnerSet",
    "compute_triple_winners",
    "evaluate_recommendation_integrity",
    "why_recommended",
]
