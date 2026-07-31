"""Unit / acceptance checks for ADR-013 Query Golden Set v1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "query_golden.v1.jsonl"
MANIFEST = ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "manifest.json"
SCHEMA = ROOT / "evaluation" / "schemas" / "query_golden_case.schema.json"
GATES = ROOT / "evaluation" / "config" / "query_golden_gates.v1.json"

BUCKET_TARGETS = {
    "fast_path": 300,
    "typo_fuzzy": 200,
    "negation_correction": 150,
    "clarification": 150,
    "llm_required": 100,
    "adversarial": 100,
}


def test_dataset_exists_and_has_1000_lines() -> None:
    assert DATASET.is_file()
    lines = [ln for ln in DATASET.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1000


def test_bucket_counts_match_targets() -> None:
    from taksitlio.evaluation.query_golden.loader import (
        load_query_golden_cases,
        summarize_buckets,
    )

    cases = load_query_golden_cases(DATASET)
    counts = summarize_buckets(cases)
    assert counts == BUCKET_TARGETS


def test_annotation_mix_hr_and_draft() -> None:
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases

    cases = load_query_golden_cases(DATASET)
    hr = sum(1 for c in cases if c.annotation.get("status") == "HUMAN_REVIEWED")
    draft = sum(1 for c in cases if c.annotation.get("status") == "DRAFT")
    assert hr == 100
    assert draft == 900


def test_cases_validate_against_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Validator = jsonschema.Draft7Validator
    validator = Validator(schema)
    errors = []
    with DATASET.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            row = json.loads(line)
            errs = sorted(validator.iter_errors(row), key=lambda e: e.path)
            if errs:
                errors.append(f"line {i} {row.get('case_id')}: {errs[0].message}")
            if len(errors) >= 5:
                break
    assert not errors, "\n".join(errors)


def test_manifest_consistent() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["total_cases"] == 1000
    assert manifest["bucket_counts"] == BUCKET_TARGETS
    assert manifest["annotation_counts"]["HUMAN_REVIEWED"] == 100


def test_gates_config_has_zero_tolerance_list() -> None:
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    assert "wrong_bank_mapping" in gates["zero_tolerance"]
    assert gates["parser_gate_thresholds"]["false_auto_resolution_count"]["max_count"] == 0


def test_loader_and_catalog_smoke() -> None:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases
    from taksitlio.query_understanding import fast_parse

    cases = load_query_golden_cases(DATASET)
    catalog = build_query_golden_test_catalog()
    # Known HR seed-style message
    sample = next(c for c in cases if "laptop" in c.message.casefold() and c.expected.get("merchant"))
    parsed = fast_parse(sample.message, catalog=catalog)
    assert parsed.merchant is not None or sample.expected.get("merchant") is None


def test_evaluate_parser_gate_bootstrap_with_drafts() -> None:
    from taksitlio.evaluation.query_golden.metrics import (
        ParserLaneMetrics,
        evaluate_parser_gate,
    )

    gates = json.loads(GATES.read_text(encoding="utf-8"))
    metrics = ParserLaneMetrics(
        case_count=1000,
        human_reviewed_count=100,
        merchant_precision=1.0,
        institution_precision=1.0,
        category_precision=1.0,
        price_extraction_accuracy=0.94,
        term_extraction_accuracy=1.0,
        negation_recall=1.0,
        correction_recall=1.0,
        clarification_accuracy=0.8,
        llm_routing_accuracy=0.7,
        false_auto_resolution_count=0,
        unnecessary_llm_on_fast_count=0,
        unnecessary_llm_on_clarification_count=0,
    )
    gate = evaluate_parser_gate(metrics, gates, draft_count=900)
    assert gate["status"] == "BOOTSTRAP"
    assert any(n.startswith("warn:") for n in gate["notes"])


def test_evaluate_parser_gate_fails_on_false_auto() -> None:
    from taksitlio.evaluation.query_golden.metrics import (
        ParserLaneMetrics,
        evaluate_parser_gate,
    )

    gates = json.loads(GATES.read_text(encoding="utf-8"))
    metrics = ParserLaneMetrics(
        case_count=10,
        human_reviewed_count=10,
        false_auto_resolution_count=1,
    )
    gate = evaluate_parser_gate(metrics, gates, draft_count=900)
    assert gate["status"] == "FAIL"


def test_seed_example_teknoksa_laptop_expected_shape() -> None:
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases

    cases = load_query_golden_cases(DATASET)
    hit = next(
        (
            c
            for c in cases
            if "Teknoksa" in c.message and "16 GB" in c.message and "laptop" in c.message.casefold()
        ),
        None,
    )
    assert hit is not None
    assert hit.expected["route"] == "FAST"
    assert hit.expected["llm_required"] is False
    assert hit.expected["merchant"]["display_name"] == "Teknosa"
    assert hit.expected["budget"]["maximum"] == 40000
