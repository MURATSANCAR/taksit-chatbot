"""Retrieval + finance TEST lane acceptance (ADR-013)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATES = ROOT / "evaluation" / "config" / "query_golden_gates.v1.json"
DATASET = ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "query_golden.v1.jsonl"


def test_retrieval_lane_passes_zero_tolerance() -> None:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases
    from taksitlio.evaluation.query_golden.retrieval import (
        evaluate_retrieval_gate,
        evaluate_retrieval_lane,
    )

    cases = load_query_golden_cases(DATASET)
    metrics, _ = evaluate_retrieval_lane(cases, catalog=build_query_golden_test_catalog())
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_retrieval_gate(metrics, gates)
    assert gate["status"] == "PASS", gate
    assert metrics.budget_filter_violations == 0
    assert metrics.merchant_filter_violations == 0
    assert metrics.negation_leak_count == 0
    assert metrics.scored_cases > 0


def test_finance_lane_passes_zero_tolerance() -> None:
    from taksitlio.evaluation.query_golden.finance import (
        evaluate_finance_gate,
        evaluate_finance_lane,
    )

    metrics, details = evaluate_finance_lane()
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_finance_gate(metrics, gates)
    assert gate["status"] == "PASS", gate
    assert metrics.eligibility_cases >= 8
    assert metrics.payment_cases >= 3
    assert metrics.wrong_monthly_payment == 0
    assert metrics.expired_campaign_shown_active == 0
    assert any(d.get("scenario_id") == "fin-v1-002" for d in details)


def test_filter_excludes_overbudget_and_wrong_merchant() -> None:
    from taksitlio.evaluation.query_golden.retrieval import (
        filter_products_for_case,
        load_test_products,
    )

    hits = filter_products_for_case(
        load_test_products(),
        merchant_display="Teknosa",
        category_display="Dizüstü Bilgisayar",
        max_price=40000,
        negative_categories=["Cep Telefonu"],
        ram_min=16,
    )
    ids = {h["product_id"] for h in hits}
    assert "tp-laptop-16-ok" in ids
    assert "tp-laptop-8-overbudget" not in ids
    assert "tp-laptop-wrong-merchant" not in ids
    assert "tp-phone-ok" not in ids
