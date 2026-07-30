"""Acceptance test for the hardening quality gate (ADR-006 §L, §M).

Runs a small subset of the v2 validation dataset against the fixture v2
catalog with ``--gate-profile hardening`` semantics and asserts:

* every rate metric carries the ProportionMetric shape;
* status ∈ {REJECT, PROVISIONAL_ACCEPT, INSUFFICIENT_REVIEWED_DATA};
* a DRAFT synthetic dataset never reaches full ACCEPT.
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
)
from taksitlio.evaluation.runner import RunnerConfig
from taksitlio.semantic_matching import SemanticMatchPolicy


REPO_ROOT = Path(__file__).resolve().parents[3]
VAL_V2_PATH = (
    REPO_ROOT
    / "evaluation"
    / "datasets"
    / "validation"
    / "tr-category-validation.v2.jsonl"
)
FIXTURE_V2_PATH = (
    REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v2.json"
)


@pytest.mark.asyncio
async def test_hardening_gate_never_flips_draft_synthetic_to_accept(tmp_path):
    if not VAL_V2_PATH.exists() or not FIXTURE_V2_PATH.exists():
        pytest.skip("v2 dataset / fixture not generated yet")

    dataset = load_jsonl(VAL_V2_PATH)
    subset = dataset.cases[:20]
    assert subset, "v2 validation set must contain at least a few cases"
    tiny = dataset.__class__(
        dataset_id=dataset.dataset_id,
        version=dataset.version,
        split=dataset.split,
        fixture_catalog_ref=dataset.fixture_catalog_ref,
        cases=tuple(subset),
        immutable_hash=dataset.immutable_hash,
    )

    handle = await build_fixture_catalog(fixture_path=FIXTURE_V2_PATH)
    try:
        policy = SemanticMatchPolicy(
            minimum_candidate_score=0.15,
            minimum_auto_select_score=0.30,
            minimum_auto_select_gap=0.05,
        )
        outcome = await run_matcher_on_dataset(
            tiny,
            handle,
            policy=policy,
            config=RunnerConfig(mode=EvaluationMode.FULL, workers=2),
        )
        report = evaluate(
            tiny,
            outcome.predictions,
            mode=EvaluationMode.FULL,
            policy={"policy_code": policy.policy_code},
            config=load_evaluation_config(),
            latency_values=outcome.latencies_ms,
            concurrency=outcome.concurrency.to_dict(),
            gate_profile="hardening",
        )
    finally:
        await dispose_fixture_catalog(handle)

    status = report.quality_gate["status"]
    assert status != "ACCEPT", (
        "DRAFT synthetic dataset must never emit final ACCEPT under any gate profile"
    )
    assert status in {"REJECT", "PROVISIONAL_ACCEPT", "INSUFFICIENT_REVIEWED_DATA"}

    for name in (
        "status_accuracy",
        "unsafe_auto_select_rate",
        "required_candidate_recall",
        "top_2_accepted_recall",
    ):
        payload = report.metrics.get(name)
        assert isinstance(payload, dict), f"{name} must be exposed as an object"
        for key in (
            "value",
            "numerator",
            "denominator",
            "support",
            "support_status",
            "confidence_interval_95",
        ):
            assert key in payload, f"{name} missing {key} in ProportionMetric payload"

    assert report.quality_gate["gate_profile"] == "hardening"
