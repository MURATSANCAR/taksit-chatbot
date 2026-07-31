"""Finance + payment lanes for Query Golden TEST scenarios (ADR-013 L4/L5-lite)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from taksitlio.campaign_catalog import (
    CampaignEligibilityInput,
    CampaignStatus,
    CampaignType,
    FinanceCampaignRecord,
    RateSnapshotRecord,
    RateType,
    evaluate_campaign_eligibility,
)
from taksitlio.payment_plan import calculate_estimate_from_rate


def default_finance_scenarios_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "evaluation"
        / "datasets"
        / "query_golden"
        / "v1"
        / "finance_scenarios.v1.jsonl"
    )


def load_finance_scenarios(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or default_finance_scenarios_path()
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _campaign_from_row(row: Mapping[str, Any]) -> FinanceCampaignRecord:
    c = row["campaign"]
    return FinanceCampaignRecord(
        campaign_code=str(c["campaign_code"]),
        institution_code=str(c["institution_code"]),
        display_name=str(c["display_name"]),
        campaign_type=CampaignType(str(c.get("campaign_type") or "INSTALLMENT")),
        status=CampaignStatus(str(c.get("status") or "ACTIVE")),
        valid_from=_parse_dt(c.get("valid_from")),
        valid_until=_parse_dt(c.get("valid_until")),
        minimum_purchase_amount=c.get("minimum_purchase_amount"),
        maximum_purchase_amount=c.get("maximum_purchase_amount"),
        eligible_terms=tuple(int(x) for x in (c.get("eligible_terms") or ())),
        excluded_terms=tuple(int(x) for x in (c.get("excluded_terms") or ())),
        eligible_merchant_codes=tuple(str(x) for x in (c.get("eligible_merchant_codes") or ())),
        eligible_category_ids=tuple(int(x) for x in (c.get("eligible_category_ids") or ())),
        agreement_active=bool(c.get("agreement_active")),
    )


@dataclass
class FinanceLaneMetrics:
    eligibility_cases: int = 0
    eligibility_accuracy: Optional[float] = None
    wrong_eligible_shown: int = 0
    wrong_rejected: int = 0
    expired_campaign_shown_active: int = 0
    no_agreement_shown: int = 0
    payment_cases: int = 0
    wrong_monthly_payment: int = 0
    wrong_total_repayment: int = 0
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_finance_lane(
    scenarios: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[FinanceLaneMetrics, list[dict[str, Any]]]:
    rows = list(scenarios) if scenarios is not None else load_finance_scenarios()
    details: list[dict[str, Any]] = []
    elig_ok = elig_tot = 0
    wrong_shown = wrong_rej = expired_shown = no_agree_shown = 0
    pay_tot = wrong_monthly = wrong_total = 0

    for row in rows:
        kind = row.get("kind") or "eligibility"
        if kind == "payment":
            pay_tot += 1
            snap = RateSnapshotRecord(
                financial_product_code=f"pay-{row['scenario_id']}",
                rate_type=RateType.ZERO_RATE if float(row.get("monthly_rate") or 0) == 0 else RateType.INTEREST,
                monthly_rate=float(row.get("monthly_rate") or 0),
                source_reference="query-golden-finance-fixture",
            )
            plan = calculate_estimate_from_rate(
                purchase_price=float(row["price"]),
                term_months=int(row["term"]),
                snapshot=snap,
                fees_total=float(row.get("fees") or 0),
                down_payment=float(row.get("down_payment") or 0),
            )
            exp_m = float(row["expected_monthly_payment"])
            exp_t = float(row["expected_total_repayment"])
            m_ok = abs(plan.monthly_payment - exp_m) <= 0.02
            t_ok = abs(plan.total_repayment - exp_t) <= 0.02
            if not m_ok:
                wrong_monthly += 1
            if not t_ok:
                wrong_total += 1
            details.append(
                {
                    "scenario_id": row["scenario_id"],
                    "kind": "payment",
                    "monthly_ok": m_ok,
                    "total_ok": t_ok,
                    "got_monthly": plan.monthly_payment,
                    "got_total": plan.total_repayment,
                }
            )
            continue

        elig_tot += 1
        camp = _campaign_from_row(row)
        inp_raw = row["input"]
        result = evaluate_campaign_eligibility(
            camp,
            CampaignEligibilityInput(
                merchant_code=str(inp_raw["merchant_code"]),
                purchase_amount=float(inp_raw["purchase_amount"]),
                term_months=int(inp_raw["term_months"]),
                category_id=inp_raw.get("category_id"),
            ),
        )
        expected = bool(row["expected_eligible"])
        ok = result.eligible is expected
        if expected and not result.eligible:
            wrong_rej += 1
        if not expected and result.eligible:
            wrong_shown += 1
            if "campaign_expired" in (row.get("expected_reasons_any") or []):
                expired_shown += 1
            if "merchant_agreement_inactive" in (row.get("expected_reasons_any") or []):
                no_agree_shown += 1
        if row.get("expected_reasons_any") and not result.eligible:
            reasons = set(result.reasons)
            if not any(r in reasons for r in row["expected_reasons_any"]):
                ok = False
        elig_ok += int(ok)
        details.append(
            {
                "scenario_id": row["scenario_id"],
                "kind": "eligibility",
                "ok": ok,
                "eligible": result.eligible,
                "reasons": list(result.reasons),
            }
        )

    metrics = FinanceLaneMetrics(
        eligibility_cases=elig_tot,
        eligibility_accuracy=(elig_ok / elig_tot) if elig_tot else None,
        wrong_eligible_shown=wrong_shown,
        wrong_rejected=wrong_rej,
        expired_campaign_shown_active=expired_shown,
        no_agreement_shown=no_agree_shown,
        payment_cases=pay_tot,
        wrong_monthly_payment=wrong_monthly,
        wrong_total_repayment=wrong_total,
        support={"eligibility": elig_tot, "payment": pay_tot},
    )
    return metrics, details


def evaluate_finance_gate(
    metrics: FinanceLaneMetrics,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = gates.get("finance_gate_thresholds") or {
        "wrong_eligible_shown": {"max_count": 0},
        "expired_campaign_shown_active": {"max_count": 0},
        "no_agreement_shown": {"max_count": 0},
        "wrong_monthly_payment": {"max_count": 0},
        "wrong_total_repayment": {"max_count": 0},
        "eligibility_accuracy": {"min": 1.0},
    }
    violations: list[str] = []

    def _max(key: str, value: int) -> None:
        rule = thresholds.get(key) or {}
        if "max_count" in rule and int(value) > int(rule["max_count"]):
            violations.append(f"{key}: {value} > {rule['max_count']}")

    def _min(key: str, value: Optional[float]) -> None:
        rule = thresholds.get(key) or {}
        if "min" in rule:
            if value is None or float(value) < float(rule["min"]):
                violations.append(f"{key}: {value} < {rule['min']}")

    _max("wrong_eligible_shown", metrics.wrong_eligible_shown)
    _max("expired_campaign_shown_active", metrics.expired_campaign_shown_active)
    _max("no_agreement_shown", metrics.no_agreement_shown)
    _max("wrong_monthly_payment", metrics.wrong_monthly_payment)
    _max("wrong_total_repayment", metrics.wrong_total_repayment)
    _min("eligibility_accuracy", metrics.eligibility_accuracy)

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "notes": list(violations),
    }
