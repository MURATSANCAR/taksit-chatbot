"""Payment reconciliation + ZERO_RATE / ZERO_TOTAL_COST (ADR-012 §9–10)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence

from taksitlio.answer_integrity.errors import PaymentReconciliationFailed
from taksitlio.answer_integrity.truth_status import CostKind
from taksitlio.campaign_catalog.models import RateType
from taksitlio.payment_plan import InstallmentLine, PaymentPlanResult


ROUNDING = ROUND_HALF_UP
MONEY_QUANT = Decimal("0.01")


def _d(value: float | int | Decimal | str) -> Decimal:
    return Decimal(str(value))


def money_round(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUNDING)


@dataclass(frozen=True)
class ReconciliationPolicy:
    absolute_tolerance: Decimal = Decimal("0.02")
    relative_tolerance: Decimal = Decimal("0.001")  # 0.1%
    calculation_method_version: str = "annuity_v1"


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    cost_kind: CostKind
    reasons: tuple[str, ...]
    calculation_method_version: str
    installments_sum: Decimal
    expected_total_repayment: Decimal


def classify_cost_kind(
    *,
    rate_type: RateType | str | None,
    fees_total: float,
    total_cost: float,
) -> CostKind:
    rt = rate_type.value if isinstance(rate_type, RateType) else rate_type
    fees = float(fees_total or 0.0)
    cost = float(total_cost or 0.0)
    if rt == RateType.ZERO_RATE.value or rt == "ZERO_RATE":
        if fees <= 0 and abs(cost) < 0.01:
            return CostKind.ZERO_TOTAL_COST
        if fees > 0 or abs(cost) >= 0.01:
            return CostKind.HAS_FEES if fees > 0 else CostKind.ZERO_RATE
        return CostKind.ZERO_RATE
    if rt in {None, "UNKNOWN", RateType.UNKNOWN.value}:
        return CostKind.UNKNOWN
    return CostKind.INTEREST_BEARING


def reconcile_payment_plan(
    plan: PaymentPlanResult,
    *,
    rate_type: RateType | str | None = None,
    source_total_repayment: Optional[float] = None,
    policy: ReconciliationPolicy | None = None,
) -> ReconciliationResult:
    pol = policy or ReconciliationPolicy()
    reasons: list[str] = []

    installments_sum = money_round(
        sum((_d(line.total_installment) for line in plan.installments), Decimal("0"))
    )
    # fees may be outside installment lines
    expected_from_lines = money_round(installments_sum + _d(plan.fees_total))
    reported_total = money_round(_d(plan.total_repayment))

    if abs(expected_from_lines - reported_total) > pol.absolute_tolerance:
        # Some calculators fold fees into total_repayment already
        if abs(installments_sum - reported_total) > pol.absolute_tolerance:
            reasons.append("installments_sum_mismatch")

    principal_plus_cost = money_round(_d(plan.financed_amount) + _d(plan.total_cost))
    if abs(principal_plus_cost - reported_total) > pol.absolute_tolerance:
        reasons.append("principal_plus_cost_mismatch")

    if source_total_repayment is not None:
        src = money_round(_d(source_total_repayment))
        diff = abs(src - reported_total)
        rel = diff / src if src > 0 else diff
        if diff > pol.absolute_tolerance and rel > pol.relative_tolerance:
            reasons.append("source_plan_tolerance_exceeded")

    # Last installment rounding: remaining balance should be ~0
    if plan.installments:
        last_balance = _d(plan.installments[-1].remaining_balance)
        if abs(last_balance) > pol.absolute_tolerance:
            reasons.append("last_installment_balance_nonzero")

    cost_kind = classify_cost_kind(
        rate_type=rate_type,
        fees_total=plan.fees_total,
        total_cost=plan.total_cost,
    )
    ok = len(reasons) == 0
    return ReconciliationResult(
        ok=ok,
        cost_kind=cost_kind,
        reasons=tuple(reasons),
        calculation_method_version=pol.calculation_method_version,
        installments_sum=installments_sum,
        expected_total_repayment=reported_total,
    )


def assert_reconciled(
    plan: PaymentPlanResult,
    **kwargs: object,
) -> ReconciliationResult:
    result = reconcile_payment_plan(plan, **kwargs)  # type: ignore[arg-type]
    if not result.ok:
        raise PaymentReconciliationFailed("; ".join(result.reasons))
    return result


def zero_rate_labels(
    cost_kind: CostKind,
    *,
    fees_total: float,
) -> tuple[str, ...]:
    """Allowed marketing labels; never emit masrafsız when fees exist."""

    labels: list[str] = []
    if cost_kind in {CostKind.ZERO_RATE, CostKind.ZERO_TOTAL_COST, CostKind.HAS_FEES}:
        if cost_kind is not CostKind.INTEREST_BEARING:
            labels.append("%0 oranlı finansman")
    if cost_kind is CostKind.ZERO_TOTAL_COST and fees_total <= 0:
        labels.append("masrafsız")
    if fees_total > 0:
        labels.append(f"Toplam ek masraf: {fees_total:.0f} TL")
    return tuple(labels)


__all__ = [
    "ReconciliationPolicy",
    "ReconciliationResult",
    "assert_reconciled",
    "classify_cost_kind",
    "money_round",
    "reconcile_payment_plan",
    "zero_rate_labels",
]
