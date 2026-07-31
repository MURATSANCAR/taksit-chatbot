"""Payment calculation gate + ZERO_RATE / ZERO_TOTAL_COST (ADR-012 §9–10)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, Sequence

from taksitlio.payment_plan import PaymentPlanResult


PAYMENT_PLAN_RECONCILIATION_FAILED = "PAYMENT_PLAN_RECONCILIATION_FAILED"


class ZeroCostLabel(str, Enum):
    ZERO_RATE = "ZERO_RATE"
    ZERO_TOTAL_COST = "ZERO_TOTAL_COST"
    HAS_FEES = "HAS_FEES"


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    outcome: str
    detail: str = ""
    tolerance: Decimal = Decimal("0.02")


def _d(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def reconcile_payment_plan(
    plan: PaymentPlanResult,
    *,
    source_monthly: Optional[float] = None,
    source_total: Optional[float] = None,
    tolerance: float = 0.02,
) -> ReconciliationResult:
    """Hide plan when installment math or source reconciliation fails."""

    tol = _d(tolerance)
    installment_sum = sum((_d(x.total_installment) for x in plan.installments), Decimal("0.00"))
    expected_total = _d(plan.total_repayment)
    # fees may be outside installment lines
    fees = _d(plan.fees_total)
    if abs(installment_sum + fees - expected_total) > tol and abs(
        installment_sum - expected_total
    ) > tol:
        return ReconciliationResult(
            ok=False,
            outcome=PAYMENT_PLAN_RECONCILIATION_FAILED,
            detail="installment_sum_mismatch",
            tolerance=tol,
        )

    principal = _d(plan.financed_amount)
    cost = _d(plan.total_cost)
    if abs(principal + cost + fees - expected_total) > tol and abs(
        principal + cost - expected_total
    ) > tol:
        # total_cost already may include fees depending on calculator
        if abs(principal + cost - expected_total) > tol:
            return ReconciliationResult(
                ok=False,
                outcome=PAYMENT_PLAN_RECONCILIATION_FAILED,
                detail="principal_cost_mismatch",
                tolerance=tol,
            )

    if source_monthly is not None:
        if abs(_d(plan.monthly_payment) - _d(source_monthly)) > tol:
            return ReconciliationResult(
                ok=False,
                outcome=PAYMENT_PLAN_RECONCILIATION_FAILED,
                detail="source_monthly_divergence",
                tolerance=tol,
            )
    if source_total is not None:
        if abs(expected_total - _d(source_total)) > tol:
            return ReconciliationResult(
                ok=False,
                outcome=PAYMENT_PLAN_RECONCILIATION_FAILED,
                detail="source_total_divergence",
                tolerance=tol,
            )

    return ReconciliationResult(ok=True, outcome="OK", tolerance=tol)


def classify_zero_cost(
    *,
    rate_is_zero: bool,
    fees_total: float,
    total_cost: float,
    purchase_price: float,
    total_repayment: float,
) -> ZeroCostLabel:
    """ZERO_RATE ≠ ZERO_TOTAL_COST. Fees block 'masrafsız'."""

    if fees_total > 0 or total_cost > 0:
        if rate_is_zero:
            return ZeroCostLabel.ZERO_RATE  # still zero rate but has fees
        return ZeroCostLabel.HAS_FEES
    if rate_is_zero and abs(total_repayment - purchase_price) < 0.02:
        return ZeroCostLabel.ZERO_TOTAL_COST
    if rate_is_zero:
        return ZeroCostLabel.ZERO_RATE
    return ZeroCostLabel.HAS_FEES


def zero_rate_user_label(label: ZeroCostLabel, *, fees_total: float = 0.0) -> str:
    if label is ZeroCostLabel.ZERO_TOTAL_COST:
        return "%0 oranlı ve ek masrafsız finansman"
    if label is ZeroCostLabel.ZERO_RATE:
        return f"%0 oranlı finansman — Toplam ek masraf: {fees_total:,.0f} TL".replace(",", ".")
    return "Faizli / masraflı finansman"


def assert_masrafsiz_allowed(label: ZeroCostLabel) -> None:
    if label is not ZeroCostLabel.ZERO_TOTAL_COST:
        raise ValueError("masrafsız label forbidden unless ZERO_TOTAL_COST")


__all__ = [
    "PAYMENT_PLAN_RECONCILIATION_FAILED",
    "ReconciliationResult",
    "ZeroCostLabel",
    "assert_masrafsiz_allowed",
    "classify_zero_cost",
    "reconcile_payment_plan",
    "zero_rate_user_label",
]
