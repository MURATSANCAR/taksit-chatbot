"""Aggregate metrics for the category-match evaluation.

None of these functions reference concrete category names — they only
use fixture keys from the loaded dataset and the predictions produced
by the runner. Error bucket names are the ones described in
ADR-005 §7 (see also ``admin/specs/category-evaluation-admin-screens.md``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from taksitlio.evaluation.domain import (
    CasePrediction,
    EvaluationCase,
    ExpectedStatus,
)


ERROR_BUCKETS = (
    "wrong_top_1_when_should_match",
    "matched_when_should_be_ambiguous",
    "matched_when_should_be_no_match",  # unsafe auto-select subset
    "ambiguous_when_should_match",
    "ambiguous_when_should_be_no_match",
    "no_match_when_should_match",
    "expected_category_missing_from_top_k",
    "forbidden_candidate_in_top_k",
    "latency_p95_over_budget",
    "dependency_failure",
)


@dataclass(frozen=True)
class ClassificationScores:
    precision: float
    recall: float
    f1: float
    support: int

    def to_dict(self) -> dict:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "support": self.support,
        }


@dataclass
class ErrorBucket:
    count: int = 0
    example_case_ids: list[str] = field(default_factory=list)

    def add(self, case_id: str, *, max_examples: int = 10) -> None:
        self.count += 1
        if len(self.example_case_ids) < max_examples:
            self.example_case_ids.append(case_id)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "example_case_ids": list(self.example_case_ids),
        }


def status_accuracy(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    if not cases:
        return 0.0
    correct = 0
    for case in cases:
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.predicted_status == case.expected.status.value:
            correct += 1
    return correct / len(cases)


def classification_scores(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
    *,
    target: ExpectedStatus,
) -> ClassificationScores:
    tp = fp = fn = 0
    for case in cases:
        pred = predictions.get(case.case_id)
        predicted = pred.predicted_status if pred else "MISSING"
        actual = case.expected.status.value
        want = target.value
        if predicted == want and actual == want:
            tp += 1
        elif predicted == want and actual != want:
            fp += 1
        elif predicted != want and actual == want:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return ClassificationScores(
        precision=precision,
        recall=recall,
        f1=f1,
        support=sum(
            1 for case in cases if case.expected.status.value == target.value
        ),
    )


def _acceptable_keys(case: EvaluationCase) -> set[str]:
    keys = set(case.expected.acceptable_fixture_keys)
    keys.update(case.expected.required_fixture_keys)
    return keys


def top_k_hit_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
    *,
    k: int,
) -> float:
    """Fraction of MATCHED / AMBIGUOUS cases whose acceptable key is in top-k."""
    total = 0
    hits = 0
    for case in cases:
        if case.expected.status is ExpectedStatus.NO_MATCH:
            continue
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        total += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        keys_in_top = {c.fixture_key for c in pred.top_k[:k]}
        if keys_in_top & acceptable:
            hits += 1
    return hits / total if total else 0.0


def top_1_accepted_accuracy(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    total = 0
    correct = 0
    for case in cases:
        if case.expected.status is not ExpectedStatus.MATCHED:
            continue
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        total += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.selected_fixture_key and pred.selected_fixture_key in acceptable:
            correct += 1
    return correct / total if total else 0.0


def top_2_accepted_recall(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    return top_k_hit_rate(cases, predictions, k=2)


def required_candidate_recall(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    denom = 0
    hits = 0
    for case in cases:
        required = set(case.expected.required_fixture_keys)
        if not required:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        seen = {c.fixture_key for c in pred.top_k}
        if required.issubset(seen):
            hits += 1
    return hits / denom if denom else 0.0


def forbidden_candidate_violation_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    denom = 0
    violations = 0
    for case in cases:
        forbidden = set(case.expected.forbidden_fixture_keys)
        if not forbidden:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        seen = {c.fixture_key for c in pred.top_k}
        if forbidden & seen:
            violations += 1
    return violations / denom if denom else 0.0


def unsafe_auto_select_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    """Fraction of NO_MATCH cases the matcher auto-selected anyway."""
    denom = 0
    unsafe = 0
    for case in cases:
        if case.expected.status is not ExpectedStatus.NO_MATCH:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.predicted_status == "MATCHED":
            unsafe += 1
    return unsafe / denom if denom else 0.0


def unnecessary_clarification_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    denom = 0
    unnecessary = 0
    for case in cases:
        if case.expected.status is not ExpectedStatus.MATCHED:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.predicted_status == "AMBIGUOUS":
            unnecessary += 1
    return unnecessary / denom if denom else 0.0


def missed_clarification_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    denom = 0
    missed = 0
    for case in cases:
        if case.expected.status is not ExpectedStatus.AMBIGUOUS:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.predicted_status == "MATCHED":
            missed += 1
    return missed / denom if denom else 0.0


def mean_reciprocal_rank(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> float:
    total = 0
    accum = 0.0
    for case in cases:
        if case.expected.status is ExpectedStatus.NO_MATCH:
            continue
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        total += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        for cand in pred.top_k:
            if cand.fixture_key in acceptable:
                accum += 1.0 / max(1, cand.rank)
                break
    return accum / total if total else 0.0


def ndcg_at_k(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
    *,
    k: int = 3,
) -> float:
    total = 0
    accum = 0.0
    for case in cases:
        if case.expected.status is ExpectedStatus.NO_MATCH:
            continue
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        total += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        dcg = 0.0
        for cand in pred.top_k[:k]:
            rel = 1.0 if cand.fixture_key in acceptable else 0.0
            dcg += rel / math.log2(1 + cand.rank)
        # ideal DCG: single acceptable item at rank 1
        idcg = 1.0 / math.log2(2)
        accum += dcg / idcg if idcg else 0.0
    return accum / total if total else 0.0


def build_error_buckets(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> dict[str, ErrorBucket]:
    buckets: dict[str, ErrorBucket] = {name: ErrorBucket() for name in ERROR_BUCKETS}
    for case in cases:
        pred = predictions.get(case.case_id)
        expected_status = case.expected.status
        acceptable = _acceptable_keys(case)
        forbidden = set(case.expected.forbidden_fixture_keys)

        if pred is None:
            buckets["dependency_failure"].add(case.case_id)
            continue

        top_keys = [c.fixture_key for c in pred.top_k]
        top_set = set(top_keys)

        if pred.predicted_status == "MATCHED":
            if expected_status is ExpectedStatus.NO_MATCH:
                buckets["matched_when_should_be_no_match"].add(case.case_id)
            elif expected_status is ExpectedStatus.AMBIGUOUS:
                buckets["matched_when_should_be_ambiguous"].add(case.case_id)
            elif expected_status is ExpectedStatus.MATCHED:
                if pred.selected_fixture_key not in acceptable:
                    buckets["wrong_top_1_when_should_match"].add(case.case_id)
        elif pred.predicted_status == "AMBIGUOUS":
            if expected_status is ExpectedStatus.MATCHED:
                buckets["ambiguous_when_should_match"].add(case.case_id)
            elif expected_status is ExpectedStatus.NO_MATCH:
                buckets["ambiguous_when_should_be_no_match"].add(case.case_id)
        elif pred.predicted_status == "NO_MATCH":
            if expected_status is ExpectedStatus.MATCHED:
                buckets["no_match_when_should_match"].add(case.case_id)
        else:
            buckets["dependency_failure"].add(case.case_id)

        if acceptable and not (top_set & acceptable) and expected_status is not ExpectedStatus.NO_MATCH:
            buckets["expected_category_missing_from_top_k"].add(case.case_id)
        if forbidden and (forbidden & top_set):
            buckets["forbidden_candidate_in_top_k"].add(case.case_id)
    return buckets


__all__ = [
    "ClassificationScores",
    "ERROR_BUCKETS",
    "ErrorBucket",
    "build_error_buckets",
    "classification_scores",
    "forbidden_candidate_violation_rate",
    "mean_reciprocal_rank",
    "missed_clarification_rate",
    "ndcg_at_k",
    "required_candidate_recall",
    "status_accuracy",
    "top_1_accepted_accuracy",
    "top_2_accepted_recall",
    "top_k_hit_rate",
    "unnecessary_clarification_rate",
    "unsafe_auto_select_rate",
]
