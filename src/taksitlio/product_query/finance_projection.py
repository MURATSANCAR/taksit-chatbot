"""Build product_finance_options projection rows (ADR-010 §50)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from taksitlio.campaign_catalog.eligibility import (
    CampaignEligibilityInput,
    evaluate_campaign_eligibility,
)
from taksitlio.campaign_catalog.models import FinanceCampaignRecord, RateSnapshotRecord
from taksitlio.ingestion.errors import RateUnavailable
from taksitlio.payment_plan import (
    PaymentPlanKind,
    PaymentPlanResult,
    calculate_estimate_from_rate,
)


@dataclass(frozen=True)
class OfferFinanceContext:
    product_offer_id: str
    merchant_id: str
    merchant_code: str
    purchase_price: float
    stock_status: str
    price_freshness: str
    category_id: Optional[int] = None


@dataclass(frozen=True)
class InstitutionTermOption:
    institution_id: str
    financial_product_code: str
    term_months: int
    rate_snapshot: RateSnapshotRecord
    campaign: Optional[FinanceCampaignRecord] = None
    rate_snapshot_id: Optional[str] = None
    campaign_id: Optional[str] = None


@dataclass(frozen=True)
class ProductFinanceOptionRow:
    product_offer_id: str
    merchant_id: str
    institution_id: str
    term_months: int
    monthly_payment: Optional[float]
    total_repayment: Optional[float]
    fees_total: float
    eligibility_status: str
    plan_kind: Optional[str]
    freshness_status: str
    campaign_id: Optional[str]
    rate_snapshot_id: Optional[str]
    display_label: Optional[str]
    ineligible_reasons: tuple[str, ...] = ()


def build_finance_option(
    offer: OfferFinanceContext,
    option: InstitutionTermOption,
) -> ProductFinanceOptionRow:
    reasons: list[str] = []
    # Crawl catalogs often lack live stock; UNKNOWN still allows estimate projection.
    # OUT_OF_STOCK / UNAVAILABLE remain blocked. Ranking still prefers real stock.
    if offer.stock_status not in {"AVAILABLE", "LIMITED", "UNKNOWN"}:
        reasons.append("stock_not_available")
    if offer.price_freshness not in {"FRESH"}:
        reasons.append("price_not_fresh")

    if option.campaign is not None:
        elig = evaluate_campaign_eligibility(
            option.campaign,
            CampaignEligibilityInput(
                merchant_code=offer.merchant_code,
                purchase_amount=offer.purchase_price,
                term_months=option.term_months,
                category_id=offer.category_id,
            ),
        )
        if not elig.eligible:
            reasons.extend(elig.reasons)

    if reasons:
        return ProductFinanceOptionRow(
            product_offer_id=offer.product_offer_id,
            merchant_id=offer.merchant_id,
            institution_id=option.institution_id,
            term_months=option.term_months,
            monthly_payment=None,
            total_repayment=None,
            fees_total=0.0,
            eligibility_status="INELIGIBLE",
            plan_kind=None,
            freshness_status=option.rate_snapshot.freshness_status,
            campaign_id=option.campaign_id,
            rate_snapshot_id=option.rate_snapshot_id,
            display_label=None,
            ineligible_reasons=tuple(reasons),
        )

    try:
        plan: PaymentPlanResult = calculate_estimate_from_rate(
            purchase_price=offer.purchase_price,
            term_months=option.term_months,
            snapshot=option.rate_snapshot,
        )
    except RateUnavailable as exc:
        return ProductFinanceOptionRow(
            product_offer_id=offer.product_offer_id,
            merchant_id=offer.merchant_id,
            institution_id=option.institution_id,
            term_months=option.term_months,
            monthly_payment=None,
            total_repayment=None,
            fees_total=0.0,
            eligibility_status="INELIGIBLE",
            plan_kind=None,
            freshness_status=option.rate_snapshot.freshness_status,
            campaign_id=option.campaign_id,
            rate_snapshot_id=option.rate_snapshot_id,
            display_label=None,
            ineligible_reasons=(f"rate_unavailable:{exc.code}",),
        )

    return ProductFinanceOptionRow(
        product_offer_id=offer.product_offer_id,
        merchant_id=offer.merchant_id,
        institution_id=option.institution_id,
        term_months=option.term_months,
        monthly_payment=plan.monthly_payment,
        total_repayment=plan.total_repayment,
        fees_total=plan.fees_total,
        eligibility_status="ELIGIBLE",
        plan_kind=PaymentPlanKind.CALCULATED_ESTIMATE.value,
        freshness_status=option.rate_snapshot.freshness_status,
        campaign_id=option.campaign_id,
        rate_snapshot_id=option.rate_snapshot_id,
        display_label=plan.display_label,
    )


def rebuild_finance_options(
    offer: OfferFinanceContext,
    options: Sequence[InstitutionTermOption],
) -> tuple[ProductFinanceOptionRow, ...]:
    return tuple(build_finance_option(offer, opt) for opt in options)


__all__ = [
    "InstitutionTermOption",
    "OfferFinanceContext",
    "ProductFinanceOptionRow",
    "build_finance_option",
    "rebuild_finance_options",
]
