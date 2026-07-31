"""Ad-hoc runner for ADR-008 P0.1 closeout.

Executes the v4 validation split under both Oracle
(``MATCHER_ORACLE_INPUT``) and E2E (``END_TO_END_RUNTIME_INPUT`` via
the deterministic FAST extractor) lanes and writes three reports:

    evaluation/reports/adr008-p01-oracle.json
    evaluation/reports/adr008-p01-e2e.json
    evaluation/reports/adr008-p01-residual-analysis.json

The residual analysis is aggregated purely from case_id + expected
fixture keys + retrieval diagnostics — no raw utterance is stored.

Run:

    python evaluation/_run_adr008_p01.py
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional

from taksitlio.evaluation import (
    EvaluationMode,
    build_fixture_catalog,
    dispose_fixture_catalog,
    evaluate,
    load_evaluation_config,
    load_jsonl,
    run_matcher_on_dataset,
)
from taksitlio.evaluation.domain import (
    EvaluationInputMode,
    ExpectedStatus,
)
from taksitlio.evaluation.runner import RunnerConfig
from taksitlio.semantic_matching import SemanticMatchPolicy


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "evaluation" / "datasets" / "validation" / "tr-category-validation.v4.jsonl"
FIXTURE_PATH = REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v3.json"
REPORTS_DIR = REPO_ROOT / "evaluation" / "reports"


def _metric_value(v: Any) -> Optional[float]:
    if isinstance(v, dict) and "value" in v:
        val = v.get("value")
        return float(val) if val is not None else None
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _metric_count(v: Any) -> Optional[int]:
    if isinstance(v, dict) and "numerator" in v:
        num = v.get("numerator")
        return int(num) if num is not None else None
    if isinstance(v, int):
        return v
    return None


def _summarise(report_metrics: Mapping[str, Any]) -> dict:
    return {
        "forbidden": int(report_metrics.get("forbidden_candidate_violation_count") or 0),
        "unsafe": int(report_metrics.get("unsafe_auto_select_count") or 0),
        "status_accuracy": _metric_value(report_metrics.get("status_accuracy")),
        "top_1_accepted_accuracy": _metric_value(
            report_metrics.get("top_1_accepted_accuracy")
        ),
        "top_2_accepted_recall": _metric_value(
            report_metrics.get("top_2_accepted_recall")
        ),
        "required_candidate_recall": _metric_value(
            report_metrics.get("required_candidate_recall")
        ),
        "candidate_recall_at_pool": _metric_value(
            report_metrics.get("candidate_recall_at_pool")
        ),
        "decision_policy_error_rate": _metric_value(
            report_metrics.get("decision_policy_error_rate")
        ),
    }


async def _run_lane(input_mode: EvaluationInputMode) -> tuple[dict, list[dict]]:
    dataset = load_jsonl(DATASET_PATH)
    handle = await build_fixture_catalog(fixture_path=FIXTURE_PATH)
    try:
        outcome = await run_matcher_on_dataset(
            dataset,
            handle,
            policy=SemanticMatchPolicy(),
            config=RunnerConfig(
                mode=EvaluationMode.FULL, workers=4, input_mode=input_mode
            ),
        )
        report = evaluate(
            dataset,
            outcome.predictions,
            mode=EvaluationMode.FULL,
            policy={"policy_code": SemanticMatchPolicy().policy_code},
            config=load_evaluation_config(),
            latency_values=outcome.latencies_ms,
            concurrency=outcome.concurrency.to_dict(),
            gate_profile="provisional",
        )
        residual = _residual_records(dataset.cases, outcome.predictions)
    finally:
        await dispose_fixture_catalog(handle)
    summary = _summarise(report.metrics)
    summary["lane"] = input_mode.value
    summary["gate"] = report.quality_gate
    summary["case_count"] = len(dataset.cases)
    return summary, residual


def _residual_records(cases, predictions) -> list[dict]:
    records: list[dict] = []
    for case in cases:
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        expected = case.expected
        required = set(expected.required_fixture_keys or ())
        acceptable = set(expected.acceptable_fixture_keys or ()) | required
        forbidden = set(expected.forbidden_fixture_keys or ())
        top_keys = [k.fixture_key for k in pred.top_k]
        top_2 = set(top_keys[:2])
        pool = set(pred.pool_fixture_keys or ())

        buckets: list[str] = []
        # Safety-related buckets first.
        if forbidden and (forbidden & set(top_keys)):
            buckets.append("FORBIDDEN_IN_TOP_K")
        if expected.status is ExpectedStatus.NO_MATCH and pred.predicted_status == "MATCHED":
            buckets.append("UNSAFE_MATCH_ON_NO_MATCH")
        # Quality buckets.
        if expected.status is not ExpectedStatus.NO_MATCH:
            if acceptable and not (acceptable & pool):
                buckets.append("RETRIEVAL_MISS")
            if acceptable and (acceptable & pool) and not (acceptable & top_2):
                buckets.append("RANKING_MISS")
            if required and not required.issubset(set(top_keys)):
                buckets.append("REQUIRED_SIBLING_MISSING")
            if (
                acceptable
                and top_keys
                and top_keys[0] not in acceptable
                and (acceptable & top_2)
            ):
                buckets.append("CORRECT_CANDIDATE_RANKED_LOW")
            if (
                acceptable
                and len(top_keys) >= 3
                and top_keys[0] not in acceptable
                and top_keys[1] not in acceptable
                and top_keys[2] in acceptable
            ):
                buckets.append("CORRECT_CANDIDATE_RANKED_3")
            if (
                expected.status is ExpectedStatus.MATCHED
                and pred.predicted_status == "AMBIGUOUS"
                and top_keys
                and top_keys[0] in acceptable
            ):
                buckets.append("DECISION_FALSE_AMBIGUITY")
        if not buckets:
            continue
        diag = pred.diagnostics or {}
        retrieval = diag.get("retrieval_diagnostic") or {}
        records.append(
            {
                "case_id": case.case_id,
                "expected_status": expected.status.value,
                "predicted_status": pred.predicted_status,
                "buckets": buckets,
                "required": sorted(required),
                "acceptable": sorted(acceptable),
                "forbidden": sorted(forbidden),
                "top_k_fixture_keys": top_keys,
                "reason_codes": retrieval.get("reason_codes") or [],
                "decision_reason_code": retrieval.get("decision_reason_code"),
                "retrieved_by": retrieval.get("retrieved_by") or {},
                "diversity_notes": retrieval.get("diversity_notes") or [],
            }
        )
    return records


def _residual_summary(records: list[dict]) -> dict:
    bucket_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for rec in records:
        for b in rec["buckets"]:
            bucket_counts[b] += 1
        for rc in rec["reason_codes"]:
            reason_counts[rc] += 1
    return {
        "residual_case_count": len(records),
        "bucket_counts": dict(bucket_counts),
        "reason_code_counts": dict(reason_counts),
    }


async def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    oracle_summary, oracle_residual = await _run_lane(
        EvaluationInputMode.MATCHER_ORACLE_INPUT
    )
    (REPORTS_DIR / "adr008-p01-oracle.json").write_text(
        json.dumps(oracle_summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print("ORACLE:", json.dumps({k: v for k, v in oracle_summary.items() if k != "gate"}, indent=2))

    e2e_summary, e2e_residual = await _run_lane(
        EvaluationInputMode.END_TO_END_RUNTIME_INPUT
    )
    (REPORTS_DIR / "adr008-p01-e2e.json").write_text(
        json.dumps(e2e_summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print("E2E:", json.dumps({k: v for k, v in e2e_summary.items() if k != "gate"}, indent=2))

    residual = {
        "dataset": DATASET_PATH.name,
        "fixture": FIXTURE_PATH.name,
        "oracle": {
            **_residual_summary(oracle_residual),
            "case_ids": [r["case_id"] for r in oracle_residual],
            "records": oracle_residual,
        },
        "e2e": {
            **_residual_summary(e2e_residual),
            "case_ids": [r["case_id"] for r in e2e_residual],
            "records": e2e_residual,
        },
    }
    (REPORTS_DIR / "adr008-p01-residual-analysis.json").write_text(
        json.dumps(residual, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print("Residual oracle:", residual["oracle"]["residual_case_count"])
    print("Residual e2e:", residual["e2e"]["residual_case_count"])


if __name__ == "__main__":
    asyncio.run(main())
