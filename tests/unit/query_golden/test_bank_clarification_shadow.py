"""Bank mapping, clarification, expanded finance, shadow smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATES = ROOT / "evaluation" / "config" / "query_golden_gates.v1.json"
DATASET = ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "query_golden.v1.jsonl"


def test_expanded_finance_payment_golden_passes() -> None:
    from taksitlio.evaluation.query_golden.finance import (
        evaluate_finance_gate,
        evaluate_finance_lane,
    )

    metrics, _ = evaluate_finance_lane()
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    assert evaluate_finance_gate(metrics, gates)["status"] == "PASS"
    assert metrics.payment_cases >= 8
    assert metrics.eligibility_cases >= 10
    assert metrics.wrong_monthly_payment == 0


def test_bank_mapping_verification_table_passes() -> None:
    from taksitlio.evaluation.query_golden.bank_mapping import (
        evaluate_bank_mapping_gate,
        evaluate_bank_mapping_lane,
    )

    metrics, details = evaluate_bank_mapping_lane()
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_bank_mapping_gate(metrics, gates)
    assert gate["status"] == "PASS", gate
    assert metrics.row_count == 10
    assert metrics.wrong_bank_mapping == 0
    assert all(d["ok"] for d in details)


def test_clarification_lane_zero_llm_leak() -> None:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.clarification import (
        evaluate_clarification_gate,
        evaluate_clarification_lane,
    )
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases

    cases = load_query_golden_cases(DATASET)
    metrics, _ = evaluate_clarification_lane(
        cases, catalog=build_query_golden_test_catalog()
    )
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_clarification_gate(metrics, gates, draft_heavy=True)
    assert metrics.unnecessary_llm_on_clarification_count == 0
    assert gate["status"] in {"PASS", "BOOTSTRAP"}


def test_shadow_smoke_runs() -> None:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases
    from taksitlio.evaluation.query_golden.shadow import (
        evaluate_shadow_gate,
        evaluate_shadow_lane,
    )

    cases = load_query_golden_cases(DATASET)[:50]
    metrics, details = evaluate_shadow_lane(
        cases, catalog=build_query_golden_test_catalog()
    )
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_shadow_gate(metrics, gates)
    assert gate["status"] == "BOOTSTRAP"
    assert metrics.compared > 0
    assert len(details) == metrics.compared
