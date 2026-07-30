"""Evaluation orchestrator: dataset + predictions → aggregated report.

The evaluator computes every metric described in ADR-005 and applies
the configurable quality gate. Objective weights and thresholds come
from ``evaluation/config/evaluation_defaults.json``; the evaluator
holds **no business constants** and does not hardcode fixture keys.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from taksitlio.evaluation import calibration, latency, metrics
from taksitlio.evaluation.domain import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationMode,
    ExpectedStatus,
    QualityGateStatus,
)
from taksitlio.evaluation.metrics import ErrorBucket


DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "config"
    / "evaluation_defaults.json"
)


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
    """Weighted linear objective; missing metric contributes 0.

    The evaluator does not decide *which* metrics matter — that is the
    config's job (ADR-005 §10). If a weight references an unknown
    metric we ignore it rather than crash so nightly runs stay green
    while the config is iterated.
    """
    total = 0.0
    for name, weight in weights.items():
        value = float(metric_values.get(name, 0.0))
        # Metrics that are "lower is better" are expected to be phrased
        # negatively in the config (weight * value where the metric is a
        # violation rate; use negative weight to penalize).
        total += float(weight) * value
    return total


def _apply_quality_gate(
    metric_values: Mapping[str, float],
    thresholds: Mapping[str, Mapping[str, float]],
    latency_summary: latency.LatencySummary,
    latency_budget_ms: Optional[float],
) -> tuple[QualityGateStatus, list[str]]:
    violations: list[str] = []
    for metric_name, rule in thresholds.items():
        value = float(metric_values.get(metric_name, 0.0))
        if "min" in rule and value < float(rule["min"]):
            violations.append(
                f"{metric_name}={value:.3f} < min {rule['min']}"
            )
        if "max" in rule and value > float(rule["max"]):
            violations.append(
                f"{metric_name}={value:.3f} > max {rule['max']}"
            )
    if latency_budget_ms is not None and latency_summary.p95_ms > latency_budget_ms:
        violations.append(
            f"latency.p95_ms={latency_summary.p95_ms:.1f} > budget "
            f"{latency_budget_ms}"
        )
    status = QualityGateStatus.ACCEPT if not violations else QualityGateStatus.REJECT
    return status, violations


def evaluate(
    dataset: EvaluationDataset,
    predictions,
    *,
    mode: EvaluationMode,
    policy: Mapping,
    config: Mapping,
    latency_values: list[float],
    concurrency: Mapping,
) -> EvaluationReport:
    cases = list(dataset.cases)

    metric_values: dict[str, float] = {}
    metric_values["status_accuracy"] = metrics.status_accuracy(cases, predictions)

    matched = metrics.classification_scores(cases, predictions, target=ExpectedStatus.MATCHED)
    ambiguous = metrics.classification_scores(cases, predictions, target=ExpectedStatus.AMBIGUOUS)
    no_match = metrics.classification_scores(cases, predictions, target=ExpectedStatus.NO_MATCH)

    metric_values["top_1_accepted_accuracy"] = metrics.top_1_accepted_accuracy(cases, predictions)
    metric_values["top_2_accepted_recall"] = metrics.top_2_accepted_recall(cases, predictions)
    metric_values["required_candidate_recall"] = metrics.required_candidate_recall(cases, predictions)
    metric_values["forbidden_candidate_violation_rate"] = metrics.forbidden_candidate_violation_rate(cases, predictions)
    metric_values["unsafe_auto_select_rate"] = metrics.unsafe_auto_select_rate(cases, predictions)
    metric_values["unnecessary_clarification_rate"] = metrics.unnecessary_clarification_rate(cases, predictions)
    metric_values["missed_clarification_rate"] = metrics.missed_clarification_rate(cases, predictions)
    metric_values["mrr"] = metrics.mean_reciprocal_rank(cases, predictions)
    metric_values["hit_rate_at_1"] = metrics.top_k_hit_rate(cases, predictions, k=1)
    metric_values["hit_rate_at_2"] = metrics.top_k_hit_rate(cases, predictions, k=2)
    metric_values["hit_rate_at_3"] = metrics.top_k_hit_rate(cases, predictions, k=3)
    metric_values["ndcg_at_3"] = metrics.ndcg_at_k(cases, predictions, k=3)

    pairs = _pairs_for_calibration(cases, predictions)
    cal = calibration.summarize(pairs, bucket_count=int(config.get("calibration_bucket_count", 10)))
    metric_values["brier"] = cal.brier
    metric_values["ece"] = cal.ece

    latency_summary = latency.summarize(latency_values)

    thresholds = config.get("quality_gate_thresholds", {})
    latency_budget_ms = float(config.get("latency_budget_p95_ms", 0.0)) or None

    gate_status, violations = _apply_quality_gate(
        metric_values, thresholds, latency_summary, latency_budget_ms
    )

    objective_weights = config.get("objective_weights", {})
    objective_score = _objective_score(metric_values, objective_weights)

    metrics_payload = {
        "status_accuracy": metric_values["status_accuracy"],
        "matched": matched.to_dict(),
        "ambiguous": ambiguous.to_dict(),
        "no_match": no_match.to_dict(),
        "top_1_accepted_accuracy": metric_values["top_1_accepted_accuracy"],
        "top_2_accepted_recall": metric_values["top_2_accepted_recall"],
        "required_candidate_recall": metric_values["required_candidate_recall"],
        "forbidden_candidate_violation_rate": metric_values["forbidden_candidate_violation_rate"],
        "unsafe_auto_select_rate": metric_values["unsafe_auto_select_rate"],
        "unnecessary_clarification_rate": metric_values["unnecessary_clarification_rate"],
        "missed_clarification_rate": metric_values["missed_clarification_rate"],
        "mrr": metric_values["mrr"],
        "hit_rate_at_1": metric_values["hit_rate_at_1"],
        "hit_rate_at_2": metric_values["hit_rate_at_2"],
        "hit_rate_at_3": metric_values["hit_rate_at_3"],
        "ndcg_at_3": metric_values["ndcg_at_3"],
        "brier": metric_values["brier"],
        "ece": metric_values["ece"],
    }

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
        # attribute latency budget breach to a synthetic bucket without
        # naming any case
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
        "violations": violations,
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
    "evaluate",
    "load_evaluation_config",
]
