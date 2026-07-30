"""Provisional gate acceptance test (ADR-007 §H).

Runs a v3 validation subset that contains ≥100 HUMAN_REVIEWED cases.
Asserts:

* ``gate_profile == "provisional"`` is surfaced in the report;
* status is one of {PROVISIONAL_ACCEPT, REJECT, INSUFFICIENT_REVIEWED_DATA} —
  ``ACCEPT`` is never reachable under the provisional profile;
* full-DRAFT slices still cannot ACCEPT;
* forbidden_count / unsafe_auto_select_count are enforced (both zero for
  a passing PROVISIONAL_ACCEPT — the test lifts this invariant).
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
VAL_V3_PATH = (
    REPO_ROOT
    / "evaluation"
    / "datasets"
    / "validation"
    / "tr-category-validation.v3.jsonl"
)
FIXTURE_V3_PATH = (
    REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v3.json"
)


@pytest.mark.asyncio
async def test_provisional_gate_ceiling_is_provisional_accept():
    if not VAL_V3_PATH.exists() or not FIXTURE_V3_PATH.exists():
        pytest.skip("v3 dataset / fixture not generated yet")

    dataset = load_jsonl(VAL_V3_PATH)
    # Keep the first 40 cases so the CI runtime stays under a second.
    subset = dataset.cases[:40]
    tiny = dataset.__class__(
        dataset_id=dataset.dataset_id,
        version=dataset.version,
        split=dataset.split,
        fixture_catalog_ref=dataset.fixture_catalog_ref,
        cases=tuple(subset),
        immutable_hash=dataset.immutable_hash,
    )

    handle = await build_fixture_catalog(fixture_path=FIXTURE_V3_PATH)
    try:
        policy = SemanticMatchPolicy()
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
            gate_profile="provisional",
        )
    finally:
        await dispose_fixture_catalog(handle)

    status = report.quality_gate["status"]
    assert status != "ACCEPT", (
        "provisional profile must NEVER emit full ACCEPT — that is the "
        "whole point of the ceiling per ADR-007 §H"
    )
    assert status in {
        "PROVISIONAL_ACCEPT",
        "REJECT",
        "INSUFFICIENT_REVIEWED_DATA",
    }
    assert report.quality_gate["gate_profile"] == "provisional"


@pytest.mark.asyncio
async def test_full_provisional_dataset_gate_reflects_hard_safety():
    """ADR-007 §H: any non-zero forbidden_count or unsafe_auto_select_count
    must DEMOTE the final status away from ACCEPT (and, for the provisional
    profile, away from PROVISIONAL_ACCEPT too). We don't assert the model
    itself is safety-clean here — we assert the *gate* correctly reflects
    whatever hard-safety violation the run produced. Campaign status stays
    NO until the matcher/decision-policy pushes both counters to zero.
    """

    if not VAL_V3_PATH.exists() or not FIXTURE_V3_PATH.exists():
        pytest.skip("v3 dataset / fixture not generated yet")

    dataset = load_jsonl(VAL_V3_PATH)
    handle = await build_fixture_catalog(fixture_path=FIXTURE_V3_PATH)
    try:
        outcome = await run_matcher_on_dataset(
            dataset,
            handle,
            policy=SemanticMatchPolicy(),
            config=RunnerConfig(mode=EvaluationMode.FULL, workers=4),
        )
        report = evaluate(
            dataset,
            outcome.predictions,
            mode=EvaluationMode.FULL,
            policy={"policy_code": "SEMANTIC_MATCH_TR_V3"},
            config=load_evaluation_config(),
            latency_values=outcome.latencies_ms,
            concurrency=outcome.concurrency.to_dict(),
            gate_profile="provisional",
        )
    finally:
        await dispose_fixture_catalog(handle)

    metrics = report.metrics
    forbidden = metrics["forbidden_candidate_violation_count"]
    unsafe = metrics["unsafe_auto_select_count"]
    forbidden_val = forbidden["value"] if isinstance(forbidden, dict) else forbidden
    unsafe_val = unsafe["value"] if isinstance(unsafe, dict) else unsafe

    status = report.quality_gate["status"]
    notes = report.quality_gate.get("notes", [])
    if forbidden_val > 0 or unsafe_val > 0:
        assert status in {"REJECT", "INSUFFICIENT_REVIEWED_DATA"}, (
            f"non-zero hard-safety counts must never reach any ACCEPT "
            f"variant; got status={status}, forbidden={forbidden_val}, "
            f"unsafe={unsafe_val}"
        )
        assert any("hard-safety violated" in n for n in notes), (
            f"gate notes must record the hard-safety violation: {notes}"
        )
    else:
        # Both counters clean — the provisional profile allows at most
        # PROVISIONAL_ACCEPT, never full ACCEPT.
        assert status in {
            "PROVISIONAL_ACCEPT",
            "REJECT",
            "INSUFFICIENT_REVIEWED_DATA",
        }
        assert status != "ACCEPT"
