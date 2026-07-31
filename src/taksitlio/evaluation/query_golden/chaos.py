"""Chaos / degrade scenarios for ADR-013 §11 (TEST expectations)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from taksitlio.campaign_catalog import (
    CampaignEligibilityInput,
    CampaignStatus,
    CampaignType,
    FinanceCampaignRecord,
    evaluate_campaign_eligibility,
)
from taksitlio.llm_routing import should_route_to_llm
from taksitlio.query_clarification.policy import should_ask_clarification
from taksitlio.query_understanding import CatalogHints, detect_gaps, fast_parse
from taksitlio.search_progress import DataOrigin, assert_truthful_message, finance_progress_message


def default_chaos_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "evaluation"
        / "datasets"
        / "query_golden"
        / "v1"
        / "chaos_scenarios.v1.jsonl"
    )


def load_chaos_scenarios(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or default_chaos_path()
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


@dataclass
class ChaosLaneMetrics:
    scenario_count: int = 0
    passed: int = 0
    failed: int = 0
    accuracy: Optional[float] = None
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_scenario(row: Mapping[str, Any], *, catalog: CatalogHints) -> tuple[bool, str]:
    kind = str(row.get("kind") or "")
    if kind == "progress_truthfulness":
        origin = row["data_origin"]
        msg = finance_progress_message(origin)
        try:
            assert_truthful_message(msg, data_origin=origin)
        except Exception as exc:  # noqa: BLE001 — scenario outcome
            return False, str(exc)
        # Fake live claim must fail when origin is local
        if row.get("assert_rejects_live_claim"):
            fake = "Finans kuruluşlarından güncel teklifler alınıyor..."
            try:
                assert_truthful_message(fake, data_origin=DataOrigin.LOCAL_VERIFIED_SNAPSHOT.value)
                return False, "fake live claim was accepted"
            except Exception:
                return True, "rejected_fake_live_claim"
        return True, "ok"

    if kind == "llm_circuit_open":
        parse = fast_parse(str(row.get("message") or "Apple almak istiyorum"), catalog=catalog)
        gaps = detect_gaps(parse)
        routed = should_route_to_llm(parse, gaps, clarification_count=0, circuit_open=True)
        ok = routed is False
        return ok, "llm_skipped" if ok else "llm_routed_while_circuit_open"

    if kind == "clarification_when_llm_down":
        parse = fast_parse(str(row.get("message") or "Apple almak istiyorum"), catalog=catalog)
        gaps = detect_gaps(parse)
        ask = should_ask_clarification(gaps=gaps, clarification_count=0, parse=parse)
        routed = should_route_to_llm(parse, gaps, clarification_count=0, circuit_open=True)
        ok = ask and not routed
        return ok, "clarification_fallback" if ok else "failed_fallback"

    if kind == "expired_campaign_not_shown":
        from datetime import datetime, timezone

        camp = FinanceCampaignRecord(
            campaign_code="chaos-expired",
            institution_code="institution-kuveyt",
            display_name="Expired",
            campaign_type=CampaignType.INSTALLMENT,
            status=CampaignStatus.ACTIVE,
            agreement_active=True,
            valid_until=datetime(2020, 1, 1, tzinfo=timezone.utc),
            eligible_merchant_codes=("merchant-teknosa",),
            eligible_terms=(12,),
        )
        result = evaluate_campaign_eligibility(
            camp,
            CampaignEligibilityInput(
                merchant_code="merchant-teknosa",
                purchase_amount=40000,
                term_months=12,
            ),
        )
        ok = result.eligible is False and "campaign_expired" in result.reasons
        return ok, "hidden" if ok else "shown"

    if kind == "stale_llm_not_applied":
        from taksitlio.llm_routing import LlmJobStatus, apply_if_fresh, create_job

        job = create_job(
            search_session_id="s1",
            query_version=1,
            conversation_state_version=1,
            input_payload={},
        )
        status, payload = apply_if_fresh(
            job,
            active_query_version=2,
            active_state_version=1,
            patch={"preferences": ["lightweight"]},
        )
        ok = status is LlmJobStatus.STALE_RESULT and payload is None
        return ok, "stale_blocked" if ok else "stale_applied"

    if kind == "empty_feed_no_crash":
        # Empty product list filter must return [] not raise
        from taksitlio.evaluation.query_golden.retrieval import filter_products_for_case

        hits = filter_products_for_case(
            [],
            merchant_display="Teknosa",
            category_display="Dizüstü Bilgisayar",
            max_price=40000,
            negative_categories=[],
            ram_min=None,
        )
        return hits == [], "empty_ok"

    return False, f"unknown_kind:{kind}"


def evaluate_chaos_lane(
    scenarios: Sequence[Mapping[str, Any]] | None = None,
    *,
    catalog: CatalogHints,
) -> tuple[ChaosLaneMetrics, list[dict[str, Any]]]:
    rows = list(scenarios) if scenarios is not None else load_chaos_scenarios()
    details: list[dict[str, Any]] = []
    passed = failed = 0
    for row in rows:
        ok, note = _run_scenario(row, catalog=catalog)
        passed += int(ok)
        failed += int(not ok)
        details.append({"scenario_id": row.get("scenario_id"), "ok": ok, "note": note})
    n = len(rows)
    metrics = ChaosLaneMetrics(
        scenario_count=n,
        passed=passed,
        failed=failed,
        accuracy=(passed / n) if n else None,
        support={"scenarios": n},
    )
    return metrics, details


def evaluate_chaos_gate(
    metrics: ChaosLaneMetrics,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = gates.get("chaos_gate_thresholds") or {"failed": {"max_count": 0}}
    violations: list[str] = []
    rule = thresholds.get("failed") or {}
    if "max_count" in rule and metrics.failed > int(rule["max_count"]):
        violations.append(f"failed: {metrics.failed} > {rule['max_count']}")
    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "notes": list(violations),
    }
