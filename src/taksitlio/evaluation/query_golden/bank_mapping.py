"""Bank–merchant mapping verification table (ADR-013 L4)."""

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
    evaluate_campaign_eligibility,
)


def default_bank_mapping_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "evaluation"
        / "datasets"
        / "query_golden"
        / "v1"
        / "bank_mapping_verification.v1.jsonl"
    )


def load_bank_mapping_rows(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or default_bank_mapping_path()
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


def _campaign_from_mapping_row(row: Mapping[str, Any]) -> FinanceCampaignRecord:
    merchants = row.get("eligible_merchant_codes")
    if not merchants:
        merchants = [row["merchant_code"]] if row.get("agreement_active") else []
    return FinanceCampaignRecord(
        campaign_code=f"map-{row['row_id']}",
        institution_code=str(row["institution_code"]),
        display_name=str(row.get("display_name") or row["institution_code"]),
        campaign_type=CampaignType.INSTALLMENT,
        status=CampaignStatus(str(row.get("campaign_status") or "ACTIVE")),
        valid_until=_parse_dt(row.get("valid_until")),
        minimum_purchase_amount=row.get("minimum_purchase_amount"),
        maximum_purchase_amount=row.get("maximum_purchase_amount"),
        eligible_terms=tuple(int(x) for x in (row.get("eligible_terms") or ())),
        excluded_terms=tuple(int(x) for x in (row.get("excluded_terms") or ())),
        eligible_merchant_codes=tuple(str(x) for x in merchants),
        eligible_category_ids=tuple(int(x) for x in (row.get("eligible_category_ids") or ())),
        agreement_active=bool(row.get("agreement_active")),
    )


@dataclass
class BankMappingMetrics:
    row_count: int = 0
    accuracy: Optional[float] = None
    wrong_bank_mapping: int = 0
    wrong_merchant_mapping: int = 0
    expired_shown: int = 0
    no_agreement_shown: int = 0
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_bank_mapping_lane(
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[BankMappingMetrics, list[dict[str, Any]]]:
    data = list(rows) if rows is not None else load_bank_mapping_rows()
    details: list[dict[str, Any]] = []
    ok_n = 0
    wrong_bank = wrong_merchant = expired = no_agree = 0

    for row in data:
        camp = _campaign_from_mapping_row(row)
        probe = row["probe"]
        result = evaluate_campaign_eligibility(
            camp,
            CampaignEligibilityInput(
                merchant_code=str(row["merchant_code"]),
                purchase_amount=float(probe["purchase_amount"]),
                term_months=int(probe["term_months"]),
                category_id=probe.get("category_id"),
            ),
        )
        expected = bool(row["expected_shown"])
        shown = result.eligible
        ok = shown is expected
        if not ok:
            if expected and not shown:
                wrong_merchant += 1
            if not expected and shown:
                wrong_bank += 1
                reason = str(row.get("reason") or "")
                if "expired" in reason:
                    expired += 1
                if "no_merchant_agreement" in reason or "agreement" in reason:
                    no_agree += 1
        ok_n += int(ok)
        details.append(
            {
                "row_id": row["row_id"],
                "ok": ok,
                "shown": shown,
                "expected_shown": expected,
                "reasons": list(result.reasons),
            }
        )

    n = len(data)
    metrics = BankMappingMetrics(
        row_count=n,
        accuracy=(ok_n / n) if n else None,
        wrong_bank_mapping=wrong_bank,
        wrong_merchant_mapping=wrong_merchant,
        expired_shown=expired,
        no_agreement_shown=no_agree,
        support={"rows": n},
    )
    return metrics, details


def evaluate_bank_mapping_gate(
    metrics: BankMappingMetrics,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = gates.get("bank_mapping_gate_thresholds") or {
        "wrong_bank_mapping": {"max_count": 0},
        "wrong_merchant_mapping": {"max_count": 0},
        "expired_shown": {"max_count": 0},
        "no_agreement_shown": {"max_count": 0},
        "accuracy": {"min": 1.0},
    }
    violations: list[str] = []
    for key in (
        "wrong_bank_mapping",
        "wrong_merchant_mapping",
        "expired_shown",
        "no_agreement_shown",
    ):
        rule = thresholds.get(key) or {}
        if "max_count" in rule and int(getattr(metrics, key)) > int(rule["max_count"]):
            violations.append(f"{key}: {getattr(metrics, key)} > {rule['max_count']}")
    rule = thresholds.get("accuracy") or {}
    if "min" in rule and (
        metrics.accuracy is None or float(metrics.accuracy) < float(rule["min"])
    ):
        violations.append(f"accuracy: {metrics.accuracy} < {rule['min']}")
    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "notes": list(violations),
    }
