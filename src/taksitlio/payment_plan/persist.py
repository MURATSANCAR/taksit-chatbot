"""Idempotent persistence for payment_plan_calculations (Recovery-P1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Optional

from taksitlio.campaign_catalog.models import RateSnapshotRecord, RateType, VerificationStatus
from taksitlio.ingestion.errors import RateUnavailable
from taksitlio.payment_plan import (
    PaymentPlanKind,
    PaymentPlanResult,
    calculate_estimate_from_rate,
    from_source_provided_offer,
    SourceProvidedOffer,
)

CALC_METHOD_VERSION = "annuity_v1"
MONEY_QUANT = Decimal("0.01")


def _safe_verification(raw: Any) -> VerificationStatus:
    text = str(raw or "UNVERIFIED")
    try:
        return VerificationStatus(text)
    except ValueError:
        return VerificationStatus.UNVERIFIED


def _d(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PersistPlanInput:
    product_offer_id: int
    institution_id: int
    financial_product_id: Optional[int]
    campaign_id: Optional[int]
    rate_snapshot_id: Optional[int]
    term_months: int
    purchase_price: Decimal
    down_payment: Decimal = Decimal("0.00")
    fees_total: Decimal = Decimal("0.00")
    plan_kind: PaymentPlanKind = PaymentPlanKind.CALCULATED_ESTIMATE
    source_monthly_payment: Optional[Decimal] = None
    source_total_repayment: Optional[Decimal] = None
    source_reference: Optional[str] = None
    first_payment_date: Optional[date] = None
    valid_until: Optional[datetime] = None


@dataclass(frozen=True)
class PersistPlanResult:
    calculation_id: Optional[int]
    status: str
    monthly_payment: Optional[Decimal]
    total_repayment: Optional[Decimal]
    verification_status: str
    detail: Mapping[str, Any]


def reconcile_plan(
    *,
    plan: PaymentPlanResult,
    source_monthly: Optional[Decimal] = None,
    source_total: Optional[Decimal] = None,
    tolerance: Decimal = Decimal("0.02"),
) -> tuple[str, dict[str, Any]]:
    """Decimal reconciliation; returns verification_status + diagnostics."""

    monthly = _d(plan.monthly_payment)
    total = _d(plan.total_repayment)
    installment_sum = sum((_d(x.total_installment) for x in plan.installments), Decimal("0.00"))
    fees = _d(plan.fees_total)
    principal = _d(plan.financed_amount)
    finance_cost = sum((_d(x.finance_cost) for x in plan.installments), Decimal("0.00"))

    diag: dict[str, Any] = {
        "monthly_payment": str(monthly),
        "total_repayment": str(total),
        "installment_sum": str(installment_sum),
        "principal_plus_cost_plus_fees": str(principal + finance_cost + fees),
    }

    if abs(installment_sum + fees - total) > tolerance and abs(installment_sum - total) > tolerance:
        return "PAYMENT_PLAN_RECONCILIATION_FAILED", {
            **diag,
            "reason": "installment_sum_mismatch",
        }

    if source_monthly is not None and abs(monthly - _d(source_monthly)) > tolerance:
        return "PAYMENT_PLAN_RECONCILIATION_FAILED", {
            **diag,
            "reason": "source_monthly_mismatch",
            "source_monthly": str(_d(source_monthly)),
        }
    if source_total is not None and abs(total - _d(source_total)) > tolerance:
        return "PAYMENT_PLAN_RECONCILIATION_FAILED", {
            **diag,
            "reason": "source_total_mismatch",
            "source_total": str(_d(source_total)),
        }
    return "VERIFIED", diag


def build_rate_record(row: Mapping[str, Any], term_rates: Mapping[int, float] | None = None) -> RateSnapshotRecord:
    return RateSnapshotRecord(
        financial_product_code=str(row.get("financial_product_code") or row.get("financial_product_id") or "unknown"),
        rate_type=RateType(str(row["rate_type"])),
        monthly_rate=float(row["monthly_rate"]) if row.get("monthly_rate") is not None else None,
        annual_cost_rate=float(row["annual_cost_rate"]) if row.get("annual_cost_rate") is not None else None,
        profit_rate=float(row["profit_rate"]) if row.get("profit_rate") is not None else None,
        minimum_amount=float(row["minimum_amount"]) if row.get("minimum_amount") is not None else None,
        maximum_amount=float(row["maximum_amount"]) if row.get("maximum_amount") is not None else None,
        minimum_term=int(row["minimum_term"]) if row.get("minimum_term") is not None else None,
        maximum_term=int(row["maximum_term"]) if row.get("maximum_term") is not None else None,
        term_rates=dict(term_rates or {}),
        freshness_status=str(row.get("freshness_status") or "UNVERIFIED"),
        verification_status=_safe_verification(row.get("verification_status")),
        valid_from=row.get("valid_from"),
        valid_until=row.get("valid_until"),
        source_reference=row.get("source_reference"),
        campaign_code=row.get("campaign_code"),
    )


async def persist_payment_plan(conn: Any, inp: PersistPlanInput, *, rate_row: Optional[Mapping[str, Any]] = None) -> PersistPlanResult:
    """Calculate + upsert payment plan. Never invents rates."""

    if rate_row is not None and str(rate_row.get("rate_type")) == "UNKNOWN":
        return PersistPlanResult(
            calculation_id=None,
            status="PAYMENT_PLAN_UNAVAILABLE",
            monthly_payment=None,
            total_repayment=None,
            verification_status="PAYMENT_PLAN_UNAVAILABLE",
            detail={"reason": "rate_type_UNKNOWN"},
        )

    try:
        if inp.plan_kind is PaymentPlanKind.SOURCE_PROVIDED_OFFER:
            if inp.source_monthly_payment is None:
                raise RateUnavailable("source monthly payment missing")
            plan = from_source_provided_offer(
                purchase_price=float(inp.purchase_price),
                term_months=inp.term_months,
                offer=SourceProvidedOffer(
                    monthly_payment=float(inp.source_monthly_payment),
                    total_repayment=float(inp.source_total_repayment)
                    if inp.source_total_repayment is not None
                    else None,
                    fees_total=float(inp.fees_total),
                    source_reference=inp.source_reference,
                ),
                down_payment=float(inp.down_payment),
            )
        else:
            if rate_row is None:
                raise RateUnavailable("rate snapshot missing")
            tiers = await conn.fetch(
                """
                SELECT term_months, monthly_rate
                FROM finance_rate_tiers
                WHERE rate_snapshot_id = $1
                """,
                int(rate_row["id"]),
            )
            term_rates = {
                int(t["term_months"]): float(t["monthly_rate"])
                for t in tiers
                if t["monthly_rate"] is not None
            }
            snap = build_rate_record(rate_row, term_rates=term_rates)
            plan = calculate_estimate_from_rate(
                purchase_price=float(inp.purchase_price),
                term_months=inp.term_months,
                snapshot=snap,
                down_payment=float(inp.down_payment),
                fees_total=float(inp.fees_total),
            )
    except RateUnavailable as exc:
        return PersistPlanResult(
            calculation_id=None,
            status="PAYMENT_PLAN_UNAVAILABLE",
            monthly_payment=None,
            total_repayment=None,
            verification_status="PAYMENT_PLAN_UNAVAILABLE",
            detail={"reason": str(exc)},
        )

    ver_status, diag = reconcile_plan(
        plan=plan,
        source_monthly=inp.source_monthly_payment,
        source_total=inp.source_total_repayment,
    )

    existing_id = await conn.fetchval(
        """
        SELECT id FROM payment_plan_calculations
        WHERE status = 'ACTIVE'
          AND product_offer_id = $1
          AND institution_id = $2
          AND COALESCE(campaign_id, 0) = COALESCE($3::bigint, 0)
          AND term_months = $4
          AND COALESCE(rate_snapshot_id, 0) = COALESCE($5::bigint, 0)
          AND calculation_method_version = $6
        LIMIT 1
        """,
        inp.product_offer_id,
        inp.institution_id,
        inp.campaign_id,
        inp.term_months,
        inp.rate_snapshot_id,
        CALC_METHOD_VERSION,
    )
    meta = {"reconcile": diag, "rate_source": plan.rate_source_reference}
    if existing_id is not None:
        calc_id = int(existing_id)
        await conn.execute(
            """
            UPDATE payment_plan_calculations SET
              monthly_payment = $2,
              total_repayment = $3,
              total_cost = $4,
              fees_total = $5,
              monthly_rate = $6,
              verification_status = $7,
              metadata = $8::jsonb,
              calculated_at = NOW(),
              valid_until = $9,
              first_payment_date = $10,
              purchase_price = $11,
              financed_amount = $12,
              display_label = $13,
              calculation_method = $14,
              plan_kind = $15,
              financial_product_id = $16
            WHERE id = $1
            """,
            calc_id,
            float(plan.monthly_payment),
            float(plan.total_repayment),
            float(plan.total_cost),
            float(plan.fees_total),
            plan.monthly_rate,
            ver_status,
            json.dumps(meta),
            inp.valid_until,
            inp.first_payment_date,
            float(inp.purchase_price),
            float(plan.financed_amount),
            plan.display_label,
            plan.calculation_method,
            plan.plan_kind.value,
            inp.financial_product_id,
        )
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO payment_plan_calculations (
                product_offer_id, institution_id, financial_product_id, campaign_id,
                rate_snapshot_id, plan_kind, purchase_price, down_payment, financed_amount,
                term_months, monthly_rate, fees_total, monthly_payment, total_repayment,
                total_cost, calculation_method, display_label, status, calculated_at,
                metadata, calculation_method_version, verification_status, valid_until,
                first_payment_date
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,'ACTIVE',NOW(),
                $18::jsonb,$19,$20,$21,$22
            )
            RETURNING id
            """,
            inp.product_offer_id,
            inp.institution_id,
            inp.financial_product_id,
            inp.campaign_id,
            inp.rate_snapshot_id,
            plan.plan_kind.value,
            float(inp.purchase_price),
            float(inp.down_payment),
            float(plan.financed_amount),
            inp.term_months,
            plan.monthly_rate,
            float(plan.fees_total),
            float(plan.monthly_payment),
            float(plan.total_repayment),
            float(plan.total_cost),
            plan.calculation_method,
            plan.display_label,
            json.dumps(meta),
            CALC_METHOD_VERSION,
            ver_status,
            inp.valid_until,
            inp.first_payment_date,
        )
        calc_id = int(row["id"])

    await conn.execute(
        "DELETE FROM payment_plan_installments WHERE calculation_id = $1",
        calc_id,
    )
    for line in plan.installments:
        await conn.execute(
            """
            INSERT INTO payment_plan_installments (
                calculation_id, installment_no, principal_amount, finance_cost,
                total_installment, remaining_balance
            ) VALUES ($1,$2,$3,$4,$5,$6)
            """,
            calc_id,
            line.installment_no,
            float(line.principal_amount),
            float(line.finance_cost),
            float(line.total_installment),
            float(line.remaining_balance),
        )

    await conn.execute(
        """
        UPDATE product_finance_options
        SET payment_plan_id = $2,
            monthly_payment = $3,
            total_repayment = $4,
            calculated_at = NOW()
        WHERE product_offer_id = $1
          AND institution_id = $5
          AND COALESCE(campaign_id,0) = COALESCE($6::bigint,0)
          AND term_months = $7
          AND eligibility_status = 'ELIGIBLE'
        """,
        inp.product_offer_id,
        calc_id,
        float(plan.monthly_payment),
        float(plan.total_repayment),
        inp.institution_id,
        inp.campaign_id,
        inp.term_months,
    )

    return PersistPlanResult(
        calculation_id=calc_id,
        status="PERSISTED",
        monthly_payment=_d(plan.monthly_payment),
        total_repayment=_d(plan.total_repayment),
        verification_status=ver_status,
        detail=diag,
    )


async def persist_eligible_finance_options(
    conn: Any,
    *,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Persist plans for all ELIGIBLE finance options that are calculable."""

    sql = """
        SELECT
          pfo.id AS option_id,
          pfo.product_offer_id,
          pfo.institution_id,
          pfo.financial_product_id,
          pfo.campaign_id,
          pfo.term_months,
          pfo.rate_snapshot_id,
          pfo.plan_kind,
          pfo.monthly_payment AS option_monthly,
          pfo.total_repayment AS option_total,
          pfo.fees_total,
          o.current_price,
          rs.id AS rate_id,
          rs.rate_type,
          rs.monthly_rate,
          rs.annual_cost_rate,
          rs.profit_rate,
          rs.minimum_amount,
          rs.maximum_amount,
          rs.minimum_term,
          rs.maximum_term,
          rs.freshness_status,
          rs.verification_status,
          rs.valid_from,
          rs.valid_until,
          rs.source_reference,
          rs.financial_product_id AS rate_financial_product_id
        FROM product_finance_options pfo
        JOIN product_offers o ON o.id = pfo.product_offer_id
        LEFT JOIN finance_rate_snapshots rs ON rs.id = pfo.rate_snapshot_id
        WHERE pfo.eligibility_status = 'ELIGIBLE'
        ORDER BY pfo.id
    """
    if limit is not None:
        rows = await conn.fetch(sql + " LIMIT $1", int(limit))
    else:
        rows = await conn.fetch(sql)

    stats = {
        "candidates": len(rows),
        "persisted": 0,
        "unavailable": 0,
        "reconciliation_failed": 0,
        "errors": 0,
    }
    for r in rows:
        try:
            rate_row = None
            if r["rate_id"] is not None:
                rate_row = {
                    "id": r["rate_id"],
                    "financial_product_id": r["rate_financial_product_id"],
                    "rate_type": r["rate_type"],
                    "monthly_rate": r["monthly_rate"],
                    "annual_cost_rate": r["annual_cost_rate"],
                    "profit_rate": r["profit_rate"],
                    "minimum_amount": r["minimum_amount"],
                    "maximum_amount": r["maximum_amount"],
                    "minimum_term": r["minimum_term"],
                    "maximum_term": r["maximum_term"],
                    "freshness_status": r["freshness_status"],
                    "verification_status": r["verification_status"],
                    "valid_from": r["valid_from"],
                    "valid_until": r["valid_until"],
                    "source_reference": r["source_reference"],
                }
            kind = PaymentPlanKind(str(r["plan_kind"] or "CALCULATED_ESTIMATE"))
            result = await persist_payment_plan(
                conn,
                PersistPlanInput(
                    product_offer_id=int(r["product_offer_id"]),
                    institution_id=int(r["institution_id"]),
                    financial_product_id=int(r["financial_product_id"])
                    if r["financial_product_id"] is not None
                    else None,
                    campaign_id=int(r["campaign_id"]) if r["campaign_id"] is not None else None,
                    rate_snapshot_id=int(r["rate_snapshot_id"])
                    if r["rate_snapshot_id"] is not None
                    else None,
                    term_months=int(r["term_months"]),
                    purchase_price=_d(r["current_price"]),
                    fees_total=_d(r["fees_total"] or 0),
                    plan_kind=kind,
                    source_monthly_payment=_d(r["option_monthly"])
                    if kind is PaymentPlanKind.SOURCE_PROVIDED_OFFER and r["option_monthly"] is not None
                    else None,
                    source_total_repayment=_d(r["option_total"])
                    if kind is PaymentPlanKind.SOURCE_PROVIDED_OFFER and r["option_total"] is not None
                    else None,
                    source_reference=(rate_row or {}).get("source_reference"),
                    valid_until=r["valid_until"],
                ),
                rate_row=rate_row,
            )
            if result.status == "PERSISTED":
                stats["persisted"] += 1
                if result.verification_status == "PAYMENT_PLAN_RECONCILIATION_FAILED":
                    stats["reconciliation_failed"] += 1
            elif result.status == "PAYMENT_PLAN_UNAVAILABLE":
                stats["unavailable"] += 1
        except Exception as exc:  # noqa: BLE001 — batch resilience
            stats["errors"] += 1
            stats.setdefault("error_samples", []).append(str(exc)[:200])
    return stats


__all__ = [
    "CALC_METHOD_VERSION",
    "PersistPlanInput",
    "PersistPlanResult",
    "build_rate_record",
    "persist_eligible_finance_options",
    "persist_payment_plan",
    "reconcile_plan",
]
