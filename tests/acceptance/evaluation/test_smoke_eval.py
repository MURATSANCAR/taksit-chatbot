"""End-to-end smoke evaluation on a tiny subset.

Asserts the report structure regardless of ACCEPT / REJECT status
(bootstrap thresholds intentionally allow either outcome).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.evaluation import (
    EvaluationMode,
    build_fixture_catalog,
    dispose_fixture_catalog,
    evaluate,
    load_evaluation_config,
    load_jsonl,
    run_matcher_on_dataset,
    write_report,
)
from taksitlio.evaluation.runner import RunnerConfig
from taksitlio.semantic_matching import SemanticMatchPolicy


REPO_ROOT = Path(__file__).resolve().parents[3]
VAL_PATH = REPO_ROOT / "evaluation" / "datasets" / "golden" / "tr-category-validation.v1.jsonl"


@pytest.mark.asyncio
async def test_smoke_eval_produces_valid_report(tmp_path):
    dataset = load_jsonl(VAL_PATH)
    subset = dataset.cases[:8]
    assert subset, "validation dataset must have cases for smoke eval"
    tiny_dataset = dataset.__class__(
        dataset_id=dataset.dataset_id,
        version=dataset.version,
        split=dataset.split,
        fixture_catalog_ref=dataset.fixture_catalog_ref,
        cases=tuple(subset),
        immutable_hash=dataset.immutable_hash,
    )

    handle = await build_fixture_catalog()
    try:
        policy = SemanticMatchPolicy(
            minimum_candidate_score=0.15,
            minimum_auto_select_score=0.25,
            minimum_auto_select_gap=0.05,
        )
        outcome = await run_matcher_on_dataset(
            tiny_dataset,
            handle,
            policy=policy,
            config=RunnerConfig(mode=EvaluationMode.FULL, workers=2),
        )
        report = evaluate(
            tiny_dataset,
            outcome.predictions,
            mode=EvaluationMode.FULL,
            policy={"policy_code": policy.policy_code},
            config=load_evaluation_config(),
            latency_values=outcome.latencies_ms,
            concurrency=outcome.concurrency.to_dict(),
        )
        assert report.quality_gate["status"] in {
            "ACCEPT",
            "REJECT",
            "PROVISIONAL_ACCEPT",
            "INSUFFICIENT_REVIEWED_DATA",
        }
        assert "status_accuracy" in report.metrics
        assert "unsafe_auto_select_rate" in report.metrics
        # DRAFT synthetic dataset must never emit final ACCEPT.
        assert report.quality_gate["status"] != "ACCEPT"
        assert isinstance(report.error_buckets, dict)
        out = write_report(report, reports_dir=tmp_path)
        assert out.exists()
        payload = out.read_text(encoding="utf-8")
        assert "utterance" not in payload  # privacy contract
    finally:
        await dispose_fixture_catalog(handle)
