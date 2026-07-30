"""Evaluation orchestrator: dataset + predictions → aggregated report.

The evaluator computes every metric described in ADR-005 / ADR-006 and
applies the configurable quality gate. Rate metrics are materialised as
:class:`ProportionMetric` objects (with Wilson CI + support status) and
the ``metrics`` payload contains the full object alongside a top-level
scalar ``value`` for backward-compatible consumers.

Objective weights and thresholds come from
``evaluation/config/evaluation_defaults.json``; the evaluator holds no
business constants and does not hardcode fixture keys.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

from taksitlio.evaluation import calibration, latency, metrics
from taksitlio.evaluation.domain import (
    AnnotationStatus,
    EvaluationCase,
    EvaluationDataset,
    EvaluationMode,
    ExpectedStatus,
    QualityGateStatus,
)
from taksitlio.evaluation.metrics import ErrorBucket, ProportionMetric


DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "config"
    / "evaluation_defaults.json"
)


# Minimum HUMAN_REVIEWED cases required to allow a final ACCEPT verdict on
# a synthetic bootstrap dataset. Below this threshold the evaluator can only
# emit PROVISIONAL_ACCEPT / INSUFFICIENT_REVIEWED_DATA (ADR-006 §7).
MIN_HUMAN_REVIEWED_FOR_ACCEPT = 100


def load_evaluation_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class EvaluationReport:
    run_id: str
    created_at: str
    dataset_ref: dict
    mode: EvaluationMode
    policy: dict
    quality_gate: dict
    metrics: dict
    latency: dict
    concurrency: dict
    error_buckets: dict
    debug_log_path: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "dataset_ref": dict(self.dataset_ref),
            "mode": self.mode.value,
            "policy": dict(self.policy),
            "quality_gate": dict(self.quality_gate),
            "metrics": dict(self.metrics),
            "latency": dict(self.latency),
            "concurrency": dict(self.concurrency),
            "error_buckets": {k: dict(v) for k, v in self.error_buckets.items()},
        }
        if self.debug_log_path:
            payload["debug_log_path"] = self.debug_log_path
        return payload


def _pairs_for_calibration(
    cases,
    predictions,
) -> list[tuple[float, bool]]:
    pairs: list[tuple[float, bool]] = []
    for case in cases:
        pred = predictions.get(case.case_id)
        if pred is None or not pred.top_k:
            continue
        top = pred.top_k[0]
        want_match = case.expected.status is ExpectedStatus.MATCHED
        acceptable = set(case.expected.acceptable_fixture_keys) | set(
            case.expected.required_fixture_keys
        )
        if want_match and acceptable:
            correct = top.fixture_key in acceptable
        else:
            correct = pred.predicted_status == case.expected.status.value
        confidence = max(0.0, min(1.0, float(top.score)))
        pairs.append((confidence, correct))
    return pairs


def _objective_score(
    metric_values: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    total = 0.0
    for name, weight in weights.items():
        raw = metric_values.get(name, 0.0)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        total += float(weight) * value
    return total


def _extract_value(candidate) -> Optional[float]:
    if candidate is None:
        return None
    if isinstance(candidate, ProportionMetric):
        return candidate.value
    if isinstance(candidate, Mapping) and "value" in candidate:
        val = candidate.get("value")
        return None if val is None else float(val)
    try:
        return float(candidate)
    except (TypeError, ValueError):
        return None


def _apply_quality_gate(
    metric_values: Mapping[str, object],
    thresholds: Mapping[str, Mapping[str, float]],
    latency_summary: latency.LatencySummary,
    latency_budget_ms: Optional[float],
) -> tuple[bool, list[str]]:
    """Return (gate_ok, violations). Missing metrics with a min-rule counts
    as a violation; a ``max_count`` rule always applies (denominator=0
    guard doesn't help — counts are absolute)."""

    violations: list[str] = []
    for metric_name, rule in thresholds.items():
        raw = metric_values.get(metric_name)
        if "max_count" in rule:
            # Absolute count: raw is either a ProportionMetric (numerator),
            # a Mapping with numerator/value, or an int.
            count = _extract_count(raw)
            if count is None:
                continue
            if count > int(rule["max_count"]):
                violations.append(
                    f"{metric_name}.count={count} > max_count {rule['max_count']}"
                )
            continue
        value = _extract_value(raw)
        if value is None:
            if "min" in rule:
                violations.append(
                    f"{metric_name}=NOT_APPLICABLE < min {rule['min']}"
                )
            continue
        if "min" in rule and value < float(rule["min"]):
            violations.append(f"{metric_name}={value:.3f} < min {rule['min']}")
        if "max" in rule and value > float(rule["max"]):
            violations.append(f"{metric_name}={value:.3f} > max {rule['max']}")
    if latency_budget_ms is not None and latency_summary.p95_ms > latency_budget_ms:
        violations.append(
            f"latency.p95_ms={latency_summary.p95_ms:.1f} > budget {latency_budget_ms}"
        )
    return (not violations), violations


def _extract_count(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, ProportionMetric):
        return int(raw.numerator)
    if isinstance(raw, Mapping):
        if "numerator" in raw and raw["numerator"] is not None:
            return int(raw["numerator"])
        if "count" in raw and raw["count"] is not None:
            return int(raw["count"])
        if "value" in raw and raw["value"] is not None:
            try:
                return int(raw["value"])
            except (TypeError, ValueError):
                return None
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _annotation_mix(cases: Sequence[EvaluationCase]) -> dict[str, int]:
    counts = {status.value: 0 for status in AnnotationStatus}
    synthetic = 0
    for case in cases:
        counts[case.annotation.status.value] = (
            counts.get(case.annotation.status.value, 0) + 1
        )
        if case.privacy.synthetic:
            synthetic += 1
    counts["synthetic"] = synthetic
    counts["total"] = len(cases)
    return counts


def _resolve_final_status(
    gate_ok: bool,
    annotation_mix: dict[str, int],
) -> tuple[QualityGateStatus, list[str]]:
    """Guardrail: DRAFT-only / synthetic-only datasets cannot ACCEPT.

    ADR-006 §7: no matter how good the metrics look, a synthetic bootstrap
    where every case is DRAFT is not a licence to promote a matcher.
    """

    notes: list[str] = []
    reviewed = annotation_mix.get(AnnotationStatus.HUMAN_REVIEWED.value, 0)
    total = annotation_mix.get("total", 0)
    synthetic = annotation_mix.get("synthetic", 0)
    has_reviewed_majority = reviewed >= MIN_HUMAN_REVIEWED_FOR_ACCEPT and (
        total <= 0 or reviewed * 2 >= total
    )

    if gate_ok:
        if has_reviewed_majority:
            return QualityGateStatus.ACCEPT, notes
        # No HUMAN_REVIEWED majority — cannot promote to full ACCEPT.
        notes.append(
            f"HUMAN_REVIEWED={reviewed} < {MIN_HUMAN_REVIEWED_FOR_ACCEPT}; "
            "PROVISIONAL_ACCEPT only"
        )
        if synthetic == total and total > 0:
            notes.append("dataset fully synthetic")
        return QualityGateStatus.PROVISIONAL_ACCEPT, notes

    # Gate failed. Distinguish "we don't have enough reviewed data" from
    # "we have data and the model is bad".
    if reviewed < MIN_HUMAN_REVIEWED_FOR_ACCEPT:
        notes.append(
            f"HUMAN_REVIEWED={reviewed} < {MIN_HUMAN_REVIEWED_FOR_ACCEPT}"
        )
        return QualityGateStatus.INSUFFICIENT_REVIEWED_DATA, notes
    return QualityGateStatus.REJECT, notes


def _metric_payload(metric_obj) -> dict:
    """Nest ProportionMetric while also exposing the flat ``value`` key
    at the top level for callers that iterate over ``metric_values``."""

    if isinstance(metric_obj, ProportionMetric):
        return metric_obj.to_dict()
    return metric_obj


def evaluate(
    dataset: EvaluationDataset,
    predictions,
    *,
    mode: EvaluationMode,
    policy: Mapping,
    config: Mapping,
    latency_values: list[float],
    concurrency: Mapping,
    gate_profile: str = "default",
) -> EvaluationReport:
    cases = list(dataset.cases)

    # Rate metrics as ProportionMetric objects.
    prop_metrics: dict[str, ProportionMetric] = {
        "status_accuracy": metrics.status_accuracy(cases, predictions),
        "top_1_accepted_accuracy": metrics.top_1_accepted_accuracy(cases, predictions),
        "top_2_accepted_recall": metrics.top_2_accepted_recall(cases, predictions),
        "required_candidate_recall": metrics.required_candidate_recall(cases, predictions),
        "forbidden_candidate_violation_rate": metrics.forbidden_candidate_violation_rate(
            cases, predictions
        ),
        "unsafe_auto_select_rate": metrics.unsafe_auto_select_rate(cases, predictions),
        "no_match_false_positive_rate": metrics.no_match_false_positive_rate(cases, predictions),
        "unnecessary_clarification_rate": metrics.unnecessary_clarification_rate(cases, predictions),
        "missed_clarification_rate": metrics.missed_clarification_rate(cases, predictions),
        "ambiguous_recall": metrics.ambiguous_recall(cases, predictions),
        "hit_rate_at_1": metrics.top_k_hit_rate(cases, predictions, k=1),
        "hit_rate_at_2": metrics.top_k_hit_rate(cases, predictions, k=2),
        "hit_rate_at_3": metrics.top_k_hit_rate(cases, predictions, k=3),
        # ADR-006 failure stage metrics.
        "candidate_recall_at_pool": metrics.candidate_recall_at_pool(cases, predictions),
        "candidate_recall_at_5": metrics.candidate_recall_at_k(cases, predictions, k=5),
        "candidate_recall_at_3": metrics.candidate_recall_at_k(cases, predictions, k=3),
        "candidate_recall_at_2": metrics.candidate_recall_at_k(cases, predictions, k=2),
        "ranking_error_rate": metrics.ranking_error_rate(cases, predictions),
        "decision_policy_error_rate": metrics.decision_policy_error_rate(cases, predictions),
    }

    matched = metrics.classification_scores(cases, predictions, target=ExpectedStatus.MATCHED)
    ambiguous = metrics.classification_scores(cases, predictions, target=ExpectedStatus.AMBIGUOUS)
    no_match = metrics.classification_scores(cases, predictions, target=ExpectedStatus.NO_MATCH)

    mrr = metrics.mean_reciprocal_rank(cases, predictions)
    ndcg = metrics.ndcg_at_k(cases, predictions, k=3)

    pairs = _pairs_for_calibration(cases, predictions)
    cal = calibration.summarize(
        pairs, bucket_count=int(config.get("calibration_bucket_count", 10))
    )

    latency_summary = latency.summarize(latency_values)

    # Gate thresholds — pick the correct profile.
    default_thresholds = config.get("quality_gate_thresholds", {}) or {}
    hardening_thresholds = (
        config.get("hardening_quality_gate_thresholds", {}) or {}
    )
    if gate_profile == "hardening":
        thresholds = hardening_thresholds or default_thresholds
    else:
        thresholds = default_thresholds

    latency_budget_ms = float(config.get("latency_budget_p95_ms", 0.0)) or None

    # Flat metric_values expected by gate + objective — ProportionMetric
    # __float__ returns the value (0.0 for NOT_APPLICABLE) so weight-based
    # objective still works, and we pass the full object so max_count rules
    # can look at ``numerator`` directly.
    metric_values: dict[str, object] = dict(prop_metrics)
    metric_values["forbidden_candidate_violation_count"] = (
        metrics.forbidden_candidate_violation_count(cases, predictions)
    )
    metric_values["unsafe_auto_select_count"] = metrics.unsafe_auto_select_count(
        cases, predictions
    )
    metric_values["mrr"] = mrr
    metric_values["ndcg_at_3"] = ndcg
    metric_values["brier"] = cal.brier
    metric_values["ece"] = cal.ece

    gate_ok, violations = _apply_quality_gate(
        metric_values, thresholds, latency_summary, latency_budget_ms
    )

    annotation_mix = _annotation_mix(cases)
    gate_status, gate_notes = _resolve_final_status(gate_ok, annotation_mix)

    # Objective score uses float() coercion — ProportionMetric.__float__.
    objective_weights = config.get("objective_weights", {})
    objective_score = _objective_score(
        {
            k: (float(v) if isinstance(v, ProportionMetric) else v)
            for k, v in metric_values.items()
        },
        objective_weights,
    )

    metrics_payload: dict[str, object] = {}
    for name, obj in prop_metrics.items():
        metrics_payload[name] = _metric_payload(obj)
    metrics_payload["matched"] = matched.to_dict()
    metrics_payload["ambiguous"] = ambiguous.to_dict()
    metrics_payload["no_match"] = no_match.to_dict()
    metrics_payload["mrr"] = mrr
    metrics_payload["ndcg_at_3"] = ndcg
    metrics_payload["brier"] = cal.brier
    metrics_payload["ece"] = cal.ece
    metrics_payload["forbidden_candidate_violation_count"] = int(
        metric_values["forbidden_candidate_violation_count"]
    )
    metrics_payload["unsafe_auto_select_count"] = int(
        metric_values["unsafe_auto_select_count"]
    )
    metrics_payload["annotation_mix"] = annotation_mix

    latency_payload = {
        "p50_ms": latency_summary.p50_ms,
        "p95_ms": latency_summary.p95_ms,
        "p99_ms": latency_summary.p99_ms,
        "mean_ms": latency_summary.mean_ms,
    }
    if latency_budget_ms is not None:
        latency_payload["budget_p95_ms"] = latency_budget_ms

    buckets = metrics.build_error_buckets(cases, predictions)
    if latency_budget_ms is not None and latency_summary.p95_ms > latency_budget_ms:
        buckets["latency_p95_over_budget"] = ErrorBucket(count=1, example_case_ids=[])

    dataset_ref = {
        "dataset_id": dataset.dataset_id,
        "version": dataset.version,
        "split": dataset.split.value,
        "case_count": len(cases),
    }
    if dataset.immutable_hash:
        dataset_ref["immutable_hash"] = dataset.immutable_hash

    quality_gate = {
        "status": gate_status.value,
        "objective_score": objective_score,
        "objective_weights": dict(objective_weights),
        "thresholds": {k: dict(v) for k, v in thresholds.items()},
        "gate_profile": gate_profile,
        "violations": violations,
        "notes": gate_notes,
    }

    return EvaluationReport(
        run_id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        dataset_ref=dataset_ref,
        mode=mode,
        policy=dict(policy),
        quality_gate=quality_gate,
        metrics=metrics_payload,
        latency=latency_payload,
        concurrency=dict(concurrency),
        error_buckets={name: bucket.to_dict() for name, bucket in buckets.items()},
    )


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "EvaluationReport",
    "MIN_HUMAN_REVIEWED_FOR_ACCEPT",
    "evaluate",
    "load_evaluation_config",
]
