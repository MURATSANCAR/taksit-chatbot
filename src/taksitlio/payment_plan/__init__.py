"""Payment plan calculator (ADR-010 §48–49).

Never invents missing rates. Personalized approval language is forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

from taksitlio.campaign_catalog.models import RateSnapshotRecord, RateType
from taksitlio.ingestion.errors import RateUnavailable


class PaymentPlanKind(str, Enum):
    CALCULATED_ESTIMATE = "CALCULATED_ESTIMATE"
    SOURCE_PROVIDED_OFFER = "SOURCE_PROVIDED_OFFER"


LABEL_ESTIMATE = "Tahmini aylık ödeme"
LABEL_SOURCE = "Kaynak tarafından bildirilen ödeme planı"
FORBIDDEN_CERTAIN_LABEL = "Kesin aylık taksitiniz"

ADR_SCOPE = "ADR-010"
PACKAGE_STATUS = "P3"


@dataclass(frozen=True)
class InstallmentLine:
    installment_no: int
    total_installment: float
    principal_amount: float
    finance_cost: float
    remaining_balance: float


@dataclass(frozen=True)
class PaymentPlanResult:
    plan_kind: PaymentPlanKind
    purchase_price: float
    down_payment: float
    financed_amount: float
    term_months: int
    monthly_rate: Optional[float]
    fees_total: float
    monthly_payment: float
    total_repayment: float
    total_cost: float
    calculation_method: str
    display_label: str
    installments: tuple[InstallmentLine, ...]
    rate_source_reference: Optional[str] = None


@dataclass(frozen=True)
class SourceProvidedOffer:
    monthly_payment: float
    total_repayment: Optional[float] = None
    fees_total: float = 0.0
    source_reference: Optional[str] = None


def resolve_monthly_rate(
    snapshot: RateSnapshotRecord,
    *,
    term_months: int,
    amount: float,
) -> float:
    """Extract a usable monthly rate or raise RateUnavailable (never invent)."""

    if snapshot.rate_type is RateType.UNKNOWN:
        raise RateUnavailable("rate_type=UNKNOWN", detail=snapshot.source_reference)

    if snapshot.freshness_status in {"EXPIRED", "SOURCE_UNAVAILABLE"}:
        raise RateUnavailable(
            f"rate freshness={snapshot.freshness_status}",
            detail=snapshot.source_reference,
        )

    if snapshot.minimum_amount is not None and amount < snapshot.minimum_amount:
        raise RateUnavailable("amount below rate snapshot minimum")
    if snapshot.maximum_amount is not None and amount > snapshot.maximum_amount:
        raise RateUnavailable("amount above rate snapshot maximum")
    if snapshot.minimum_term is not None and term_months < snapshot.minimum_term:
        raise RateUnavailable("term below rate snapshot minimum")
    if snapshot.maximum_term is not None and term_months > snapshot.maximum_term:
        raise RateUnavailable("term above rate snapshot maximum")

    if snapshot.rate_type is RateType.ZERO_RATE:
        return 0.0

    if term_months in snapshot.term_rates:
        return float(snapshot.term_rates[term_months])

    if snapshot.monthly_rate is not None:
        return float(snapshot.monthly_rate)

    raise RateUnavailable("monthly_rate missing on snapshot", detail=snapshot.source_reference)


def calculate_annuity_payment(principal: float, monthly_rate: float, term_months: int) -> float:
    if term_months <= 0:
        raise ValueError("term_months must be > 0")
    if principal < 0:
        raise ValueError("principal must be >= 0")
    if monthly_rate < 0:
        raise ValueError("monthly_rate must be >= 0")
    if monthly_rate == 0:
        return round(principal / term_months, 2)
    r = monthly_rate
    factor = (1 + r) ** term_months
    payment = principal * r * factor / (factor - 1)
    return round(payment, 2)


def build_installment_schedule(
    *,
    principal: float,
    monthly_rate: float,
    term_months: int,
    monthly_payment: float,
) -> tuple[InstallmentLine, ...]:
    lines: list[InstallmentLine] = []
    balance = principal
    for i in range(1, term_months + 1):
        interest = round(balance * monthly_rate, 2)
        principal_part = round(monthly_payment - interest, 2)
        if i == term_months:
            principal_part = round(balance, 2)
            payment = round(principal_part + interest, 2)
        else:
            payment = monthly_payment
        balance = round(max(0.0, balance - principal_part), 2)
        lines.append(
            InstallmentLine(
                installment_no=i,
                total_installment=payment,
                principal_amount=principal_part,
                finance_cost=interest,
                remaining_balance=balance,
            )
        )
    return tuple(lines)


def calculate_estimate_from_rate(
    *,
    purchase_price: float,
    term_months: int,
    snapshot: RateSnapshotRecord,
    down_payment: float = 0.0,
    fees_total: float = 0.0,
) -> PaymentPlanResult:
    financed = round(purchase_price - down_payment, 2)
    rate = resolve_monthly_rate(snapshot, term_months=term_months, amount=financed)
    monthly = calculate_annuity_payment(financed, rate, term_months)
    schedule = build_installment_schedule(
        principal=financed,
        monthly_rate=rate,
        term_months=term_months,
        monthly_payment=monthly,
    )
    total_repay = round(sum(x.total_installment for x in schedule) + fees_total, 2)
    return PaymentPlanResult(
        plan_kind=PaymentPlanKind.CALCULATED_ESTIMATE,
        purchase_price=purchase_price,
        down_payment=down_payment,
        financed_amount=financed,
        term_months=term_months,
        monthly_rate=rate,
        fees_total=fees_total,
        monthly_payment=monthly,
        total_repayment=total_repay,
        total_cost=round(total_repay - financed, 2),
        calculation_method="annuity_from_rate_snapshot",
        display_label=LABEL_ESTIMATE,
        installments=schedule,
        rate_source_reference=snapshot.source_reference,
    )


def from_source_provided_offer(
    *,
    purchase_price: float,
    term_months: int,
    offer: SourceProvidedOffer,
    down_payment: float = 0.0,
) -> PaymentPlanResult:
    financed = round(purchase_price - down_payment, 2)
    monthly = round(float(offer.monthly_payment), 2)
    total = (
        round(float(offer.total_repayment), 2)
        if offer.total_repayment is not None
        else round(monthly * term_months + offer.fees_total, 2)
    )
    # Equal split approximation for source-provided schedules without detail.
    principal_each = round(financed / term_months, 2)
    lines = []
    balance = financed
    for i in range(1, term_months + 1):
        principal_part = principal_each if i < term_months else round(balance, 2)
        finance_cost = round(monthly - principal_part, 2)
        balance = round(max(0.0, balance - principal_part), 2)
        lines.append(
            InstallmentLine(
                installment_no=i,
                total_installment=monthly if i < term_months else round(principal_part + max(finance_cost, 0), 2),
                principal_amount=principal_part,
                finance_cost=max(finance_cost, 0.0),
                remaining_balance=balance,
            )
        )
    return PaymentPlanResult(
        plan_kind=PaymentPlanKind.SOURCE_PROVIDED_OFFER,
        purchase_price=purchase_price,
        down_payment=down_payment,
        financed_amount=financed,
        term_months=term_months,
        monthly_rate=None,
        fees_total=offer.fees_total,
        monthly_payment=monthly,
        total_repayment=total,
        total_cost=round(total - financed, 2),
        calculation_method="source_provided",
        display_label=LABEL_SOURCE,
        installments=tuple(lines),
        rate_source_reference=offer.source_reference,
    )


def assert_safe_display_label(label: str) -> None:
    if label.strip().casefold() == FORBIDDEN_CERTAIN_LABEL.casefold():
        raise ValueError("forbidden certain-payment label")


__all__ = [
    "ADR_SCOPE",
    "FORBIDDEN_CERTAIN_LABEL",
    "LABEL_ESTIMATE",
    "LABEL_SOURCE",
    "PACKAGE_STATUS",
    "InstallmentLine",
    "PaymentPlanKind",
    "PaymentPlanResult",
    "SourceProvidedOffer",
    "assert_safe_display_label",
    "build_installment_schedule",
    "calculate_annuity_payment",
    "calculate_estimate_from_rate",
    "from_source_provided_offer",
    "resolve_monthly_rate",
]
