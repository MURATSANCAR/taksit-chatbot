"""Product data / perf / chaos lane tests (ADR-013)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATES = ROOT / "evaluation" / "config" / "query_golden_gates.v1.json"
DATASET = ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "query_golden.v1.jsonl"
PRODUCTS = ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "product_golden.v1.jsonl"


def test_product_golden_has_100_across_three_types() -> None:
    from taksitlio.evaluation.query_golden.product_data import (
        evaluate_product_data_gate,
        evaluate_product_data_lane,
        load_product_golden,
    )

    rows = load_product_golden(PRODUCTS)
    assert len(rows) == 100
    types = {r["merchant_type"] for r in rows}
    assert types == {"api_feed", "html_jsonld", "store_only"}
    metrics, _ = evaluate_product_data_lane(rows)
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_product_data_gate(metrics, gates)
    assert gate["status"] == "PASS", gate
    assert metrics.wrong_name == 0
    assert metrics.invented_products_on_store_only == 0
    assert metrics.defect_detection_misses == 0


def test_perf_lane_produces_percentiles() -> None:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases
    from taksitlio.evaluation.query_golden.perf import evaluate_perf_gate, evaluate_perf_lane

    cases = load_query_golden_cases(DATASET)[:100]
    metrics, _ = evaluate_perf_lane(
        cases, catalog=build_query_golden_test_catalog(), warm_iters=1
    )
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_perf_gate(metrics, gates)
    assert metrics.parser_p95_ms is not None
    assert gate["status"] in {"PASS", "BOOTSTRAP"}


def test_chaos_lane_all_pass() -> None:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.chaos import evaluate_chaos_gate, evaluate_chaos_lane

    metrics, details = evaluate_chaos_lane(catalog=build_query_golden_test_catalog())
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    gate = evaluate_chaos_gate(metrics, gates)
    assert gate["status"] == "PASS", details
    assert metrics.failed == 0
    assert metrics.scenario_count >= 8
