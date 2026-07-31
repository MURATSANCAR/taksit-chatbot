"""ADR-008 P0.1 quality gate acceptance tests.

Assert that the provisional profile:

* returns ``QUALITY_READY_RUNTIME_BLOCKED`` (never ``PROVISIONAL_ACCEPT``)
  when the model clears the P0.1 quality bar
  (``top_2_accepted_recall >= 0.90`` and ``required_candidate_recall >=
  0.88`` and ``forbidden = unsafe = 0``);
* returns ``QUALITY_REJECT`` (never any ACCEPT variant) when either the
  top_2 or required floor is missed;
* never lets non-zero hard-safety counts through as an ACCEPT variant
  regardless of ranking metrics.

The gate is evaluated end-to-end via ``evaluator.evaluate`` with
synthetic ProportionMetric inputs so the test is deterministic and
does not depend on model output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.evaluation import load_evaluation_config
from taksitlio.evaluation.domain import EvaluationCase, EvaluationDataset
from taksitlio.evaluation.evaluator import (
    _apply_quality_gate,
    _resolve_final_status,
)


PROVISIONAL_KEY = "provisional_quality_gate_thresholds"


class _Latency:
    p95_ms = 0.0


def _proportion(value: float, denom: int = 100) -> dict:
    return {
        "value": value,
        "numerator": int(round(value * denom)),
        "denominator": denom,
    }


def _count(numer: int, denom: int = 100) -> dict:
    return {
        "value": numer / max(1, denom),
        "numerator": int(numer),
        "denominator": denom,
    }


def _load_thresholds() -> dict:
    return load_evaluation_config()[PROVISIONAL_KEY]


def _p01_metric_bundle(
    *,
    top_1: float = 0.75,
    top_2: float = 0.90,
    required: float = 0.88,
    status: float = 0.80,
    forbidden: int = 0,
    unsafe: int = 0,
    unnecessary: float = 0.10,
    invalid_schema: float = 0.0,
    dependency_failure: float = 0.0,
) -> dict:
    return {
        "status_accuracy": _proportion(status),
        "top_1_accepted_accuracy": _proportion(top_1),
        "top_2_accepted_recall": _proportion(top_2),
        "required_candidate_recall": _proportion(required),
        "unsafe_auto_select_count": _count(unsafe),
        "forbidden_candidate_violation_count": _count(forbidden),
        "unnecessary_clarification_rate": _proportion(unnecessary),
        "invalid_schema_rate": _proportion(invalid_schema),
        "dependency_failure_rate": _proportion(dependency_failure),
    }


def _reviewed_mix(reviewed: int = 200, synthetic: int = 0) -> dict[str, int]:
    total = reviewed + synthetic
    return {
        "HUMAN_REVIEWED": reviewed,
        "DRAFT": 0,
        "synthetic": synthetic,
        "total": total,
    }


def test_p01_top2_and_required_at_target_yields_quality_ready_runtime_blocked() -> None:
    thresholds = _load_thresholds()
    metrics = _p01_metric_bundle(top_2=0.90, required=0.88)
    gate_ok, violations = _apply_quality_gate(
        metrics, thresholds, latency_summary=_Latency(), latency_budget_ms=None
    )
    assert gate_ok, f"gate should be OK; violations={violations}"
    status, _notes = _resolve_final_status(
        gate_ok,
        _reviewed_mix(),
        forbidden_count=0,
        unsafe_count=0,
        gate_profile="provisional",
    )
    assert status.value == "QUALITY_READY_RUNTIME_BLOCKED", (
        f"P0.1 provisional gate must NEVER escalate to PROVISIONAL_ACCEPT "
        f"while runtime deps are BLOCKED_DEPENDENCY; got {status.value!r}"
    )


def test_p01_below_top2_target_is_quality_reject() -> None:
    thresholds = _load_thresholds()
    metrics = _p01_metric_bundle(top_2=0.89, required=0.88)
    gate_ok, violations = _apply_quality_gate(
        metrics, thresholds, latency_summary=_Latency(), latency_budget_ms=None
    )
    assert not gate_ok
    assert any("top_2_accepted_recall" in v for v in violations)
    status, _notes = _resolve_final_status(
        gate_ok,
        _reviewed_mix(),
        forbidden_count=0,
        unsafe_count=0,
        gate_profile="provisional",
    )
    assert status.value == "QUALITY_REJECT"


def test_p01_below_required_target_is_quality_reject() -> None:
    thresholds = _load_thresholds()
    metrics = _p01_metric_bundle(top_2=0.90, required=0.87)
    gate_ok, _violations = _apply_quality_gate(
        metrics, thresholds, latency_summary=_Latency(), latency_budget_ms=None
    )
    assert not gate_ok
    status, _notes = _resolve_final_status(
        gate_ok,
        _reviewed_mix(),
        forbidden_count=0,
        unsafe_count=0,
        gate_profile="provisional",
    )
    assert status.value == "QUALITY_REJECT"


def test_p01_hard_safety_violation_blocks_any_accept_variant() -> None:
    thresholds = _load_thresholds()
    # Metrics comfortably above the P0.1 bar — but forbidden count is nonzero.
    metrics = _p01_metric_bundle(top_2=0.95, required=0.94, forbidden=3)
    gate_ok, _violations = _apply_quality_gate(
        metrics, thresholds, latency_summary=_Latency(), latency_budget_ms=None
    )
    assert not gate_ok
    status, _notes = _resolve_final_status(
        gate_ok,
        _reviewed_mix(),
        forbidden_count=3,
        unsafe_count=0,
        gate_profile="provisional",
    )
    # Ranking pass is irrelevant when hard-safety fails; must reject.
    assert status.value == "REJECT"
