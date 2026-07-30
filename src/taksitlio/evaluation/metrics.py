"""Aggregate metrics for the category-match evaluation.

Every rate metric is materialised as a :class:`ProportionMetric` so
callers see the numerator, the denominator, the sample support and a
Wilson score 95% confidence interval. When the denominator is zero the
metric returns ``value=None`` with ``support_status="NOT_APPLICABLE"``
so downstream gates never treat a missing measurement as ``0.0``.

None of these functions reference concrete category names — they only
use fixture keys from the loaded dataset and the predictions produced
by the runner. Error bucket names extend the ADR-005 §7 list with the
failure-stage buckets introduced in ADR-006.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from taksitlio.evaluation.domain import (
    CasePrediction,
    EvaluationCase,
    ExpectedStatus,
)


ERROR_BUCKETS = (
    # Existing ADR-005 buckets — kept for backward compatibility.
    "wrong_top_1_when_should_match",
    "matched_when_should_be_ambiguous",
    "matched_when_should_be_no_match",
    "ambiguous_when_should_match",
    "ambiguous_when_should_be_no_match",
    "no_match_when_should_match",
    "expected_category_missing_from_top_k",
    "forbidden_candidate_in_top_k",
    "latency_p95_over_budget",
    "dependency_failure",
    # ADR-006 failure-stage buckets.
    "RETRIEVAL_MISS",
    "RANKING_MISS",
    "DECISION_FALSE_AMBIGUITY",
    "DECISION_UNSAFE_MATCH",
    "NEGATIVE_CONSTRAINT_VIOLATION",
    "HIERARCHY_DUPLICATE_AMBIGUITY",
)


DEFAULT_MINIMUM_METRIC_SUPPORT = 30
DEFAULT_LOW_SUPPORT_WARNING = 60
DEFAULT_MINIMUM_SEGMENT_SUPPORT = 20


@dataclass(frozen=True)
class ProportionMetric:
    """Rate metric with an explicit numerator, denominator and Wilson CI.

    ``value`` is ``None`` when the metric was not applicable (denominator
    was zero). Downstream code must handle ``None`` explicitly instead of
    coercing to ``0.0``; that is the whole point of the type.
    """

    metric: str
    value: Optional[float]
    numerator: int
    denominator: int
    support: int
    support_status: str  # "OK" | "LOW_SUPPORT" | "NOT_APPLICABLE"
    confidence_interval_95: Optional[dict] = None  # {"lower": float, "upper": float}

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "value": self.value,
            "numerator": int(self.numerator),
            "denominator": int(self.denominator),
            "support": int(self.support),
            "support_status": self.support_status,
            "confidence_interval_95": (
                dict(self.confidence_interval_95)
                if self.confidence_interval_95 is not None
                else None
            ),
        }

    # Make ProportionMetric usable as a raw float in older config paths.
    def __float__(self) -> float:
        return 0.0 if self.value is None else float(self.value)


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


# ---------------------------------------------------------------------------
# Wilson score confidence interval
# ---------------------------------------------------------------------------


def wilson_confidence_interval(
    successes: int,
    trials: int,
    *,
    z: float = 1.959963984540054,
) -> Optional[dict]:
    """95% Wilson score interval for a binomial proportion.

    ``z`` defaults to the 97.5th percentile of the standard normal
    (two-sided 95% CI). Returns ``None`` if ``trials <= 0``.
    """

    if trials <= 0:
        return None
    n = float(trials)
    p_hat = successes / n
    denom = 1.0 + (z * z) / n
    centre = (p_hat + (z * z) / (2.0 * n)) / denom
    margin = (
        z
        * math.sqrt((p_hat * (1.0 - p_hat) + (z * z) / (4.0 * n)) / n)
        / denom
    )
    return {
        "lower": max(0.0, centre - margin),
        "upper": min(1.0, centre + margin),
    }


def _support_status(
    denominator: int,
    *,
    minimum_support: int = DEFAULT_MINIMUM_METRIC_SUPPORT,
    low_support_warning: int = DEFAULT_LOW_SUPPORT_WARNING,
) -> str:
    if denominator <= 0:
        return "NOT_APPLICABLE"
    if denominator < minimum_support:
        return "LOW_SUPPORT"
    if denominator < low_support_warning:
        return "LOW_SUPPORT"
    return "OK"


def _proportion(
    metric: str,
    numerator: int,
    denominator: int,
    *,
    minimum_support: int = DEFAULT_MINIMUM_METRIC_SUPPORT,
    low_support_warning: int = DEFAULT_LOW_SUPPORT_WARNING,
) -> ProportionMetric:
    if denominator <= 0:
        return ProportionMetric(
            metric=metric,
            value=None,
            numerator=0,
            denominator=0,
            support=0,
            support_status="NOT_APPLICABLE",
            confidence_interval_95=None,
        )
    value = numerator / denominator
    ci = wilson_confidence_interval(numerator, denominator)
    status = _support_status(
        denominator,
        minimum_support=minimum_support,
        low_support_warning=low_support_warning,
    )
    return ProportionMetric(
        metric=metric,
        value=value,
        numerator=int(numerator),
        denominator=int(denominator),
        support=int(denominator),
        support_status=status,
        confidence_interval_95=ci,
    )


# ---------------------------------------------------------------------------
# Classification metrics
# ---------------------------------------------------------------------------


def status_accuracy(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    total = len(cases)
    correct = 0
    for case in cases:
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.predicted_status == case.expected.status.value:
            correct += 1
    return _proportion("status_accuracy", correct, total)


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


# ---------------------------------------------------------------------------
# Retrieval / ranking metrics
# ---------------------------------------------------------------------------


def _acceptable_keys(case: EvaluationCase) -> set[str]:
    keys = set(case.expected.acceptable_fixture_keys)
    keys.update(case.expected.required_fixture_keys)
    return keys


def top_k_hit_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
    *,
    k: int,
    metric_name: Optional[str] = None,
) -> ProportionMetric:
    denominator = 0
    hits = 0
    for case in cases:
        if case.expected.status is ExpectedStatus.NO_MATCH:
            continue
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        denominator += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        keys_in_top = {c.fixture_key for c in pred.top_k[:k]}
        if keys_in_top & acceptable:
            hits += 1
    return _proportion(metric_name or f"hit_rate_at_{k}", hits, denominator)


def top_1_accepted_accuracy(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    denom = 0
    correct = 0
    for case in cases:
        if case.expected.status is not ExpectedStatus.MATCHED:
            continue
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.selected_fixture_key and pred.selected_fixture_key in acceptable:
            correct += 1
    return _proportion("top_1_accepted_accuracy", correct, denom)


def top_2_accepted_recall(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    return top_k_hit_rate(
        cases, predictions, k=2, metric_name="top_2_accepted_recall"
    )


def required_candidate_recall(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
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
    return _proportion("required_candidate_recall", hits, denom)


def forbidden_candidate_violation_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
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
    return _proportion(
        "forbidden_candidate_violation_rate", violations, denom
    )


def forbidden_candidate_violation_count(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> int:
    """Absolute number of cases whose top_k contains a forbidden key.

    ADR-006: the gate should fail on ``count > 0`` regardless of denominator
    support, since even one forbidden appearance is a hard-safety event.
    """

    count = 0
    for case in cases:
        forbidden = set(case.expected.forbidden_fixture_keys)
        if not forbidden:
            continue
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        seen = {c.fixture_key for c in pred.top_k}
        if forbidden & seen:
            count += 1
    return count


def unsafe_auto_select_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
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
    return _proportion("unsafe_auto_select_rate", unsafe, denom)


def unsafe_auto_select_count(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> int:
    count = 0
    for case in cases:
        if case.expected.status is not ExpectedStatus.NO_MATCH:
            continue
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.predicted_status == "MATCHED":
            count += 1
    return count


def no_match_false_positive_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    """Same numerator as unsafe_auto_select_rate but exposed as a distinct
    metric name so the gate can distinguish "we picked something" from
    "we produced NO_MATCH incorrectly."""

    return _proportion(
        "no_match_false_positive_rate",
        unsafe_auto_select_count(cases, predictions),
        sum(
            1
            for case in cases
            if case.expected.status is ExpectedStatus.NO_MATCH
        ),
    )


def unnecessary_clarification_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
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
    return _proportion("unnecessary_clarification_rate", unnecessary, denom)


def missed_clarification_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
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
    return _proportion("missed_clarification_rate", missed, denom)


def ambiguous_recall(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    """How often the matcher raises AMBIGUOUS on truly ambiguous cases."""

    denom = 0
    hits = 0
    for case in cases:
        if case.expected.status is not ExpectedStatus.AMBIGUOUS:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if pred.predicted_status == "AMBIGUOUS":
            hits += 1
    return _proportion("ambiguous_recall", hits, denom)


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
        idcg = 1.0 / math.log2(2)
        accum += dcg / idcg if idcg else 0.0
    return accum / total if total else 0.0


# ---------------------------------------------------------------------------
# Failure-stage metrics (ADR-006)
# ---------------------------------------------------------------------------


def _pool_keys(pred: CasePrediction) -> set[str]:
    """Union of the candidate pool with the returned top-k.

    The runner writes the pool onto the prediction when known; for older
    predictions we fall back to the returned top_k, which is a strict
    subset of the pool.
    """

    keys = {c.fixture_key for c in pred.top_k}
    keys.update(pred.pool_fixture_keys or ())
    return keys


def candidate_recall_at_pool(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    denom = 0
    hits = 0
    for case in cases:
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        denom += 1
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        if acceptable & _pool_keys(pred):
            hits += 1
    return _proportion("candidate_recall_at_pool", hits, denom)


def candidate_recall_at_k(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
    *,
    k: int,
) -> ProportionMetric:
    return top_k_hit_rate(
        cases,
        predictions,
        k=k,
        metric_name=f"candidate_recall_at_{k}",
    )


def ranking_error_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    """Fraction of retrievable cases whose acceptable key was in the pool
    but did not survive into the top-2 ranking."""

    denom = 0
    errors = 0
    for case in cases:
        acceptable = _acceptable_keys(case)
        if not acceptable:
            continue
        pred = predictions.get(case.case_id)
        if pred is None:
            continue
        pool = _pool_keys(pred)
        if not (pool & acceptable):
            continue
        denom += 1
        top_2 = {c.fixture_key for c in pred.top_k[:2]}
        if not (top_2 & acceptable):
            errors += 1
    return _proportion("ranking_error_rate", errors, denom)


def decision_policy_error_rate(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> ProportionMetric:
    """Fraction of cases whose acceptable key was top-1 but decision status
    still disagreed with the expected status."""

    denom = 0
    errors = 0
    for case in cases:
        acceptable = _acceptable_keys(case)
        pred = predictions.get(case.case_id)
        if pred is None or not pred.top_k:
            continue
        top = pred.top_k[0]
        if acceptable and top.fixture_key not in acceptable:
            continue
        denom += 1
        if pred.predicted_status != case.expected.status.value:
            errors += 1
    return _proportion("decision_policy_error_rate", errors, denom)


# ---------------------------------------------------------------------------
# Error bucket classification (ADR-006)
# ---------------------------------------------------------------------------


def _classify_failure_stage(
    case: EvaluationCase,
    pred: CasePrediction,
) -> Optional[str]:
    acceptable = _acceptable_keys(case)
    forbidden = set(case.expected.forbidden_fixture_keys)
    top_keys = [c.fixture_key for c in pred.top_k]
    top_2 = set(top_keys[:2])
    pool = _pool_keys(pred)

    if forbidden and (forbidden & set(top_keys)):
        return "NEGATIVE_CONSTRAINT_VIOLATION"

    if case.expected.status is ExpectedStatus.NO_MATCH:
        if pred.predicted_status == "MATCHED":
            return "DECISION_UNSAFE_MATCH"
        return None

    if acceptable and not (acceptable & pool):
        return "RETRIEVAL_MISS"

    if acceptable and (acceptable & pool) and not (acceptable & top_2):
        return "RANKING_MISS"

    if (
        acceptable
        and top_keys
        and top_keys[0] in acceptable
        and pred.predicted_status == "AMBIGUOUS"
        and case.expected.status is ExpectedStatus.MATCHED
    ):
        return "DECISION_FALSE_AMBIGUITY"

    return None


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

        # Existing coarse-grained buckets (kept for backward compat).
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

        if (
            acceptable
            and not (top_set & acceptable)
            and expected_status is not ExpectedStatus.NO_MATCH
        ):
            buckets["expected_category_missing_from_top_k"].add(case.case_id)
        if forbidden and (forbidden & top_set):
            buckets["forbidden_candidate_in_top_k"].add(case.case_id)

        # ADR-006 failure-stage classification.
        stage = _classify_failure_stage(case, pred)
        if stage is not None:
            buckets[stage].add(case.case_id)
    return buckets


__all__ = [
    "ClassificationScores",
    "ERROR_BUCKETS",
    "ErrorBucket",
    "ProportionMetric",
    "ambiguous_recall",
    "build_error_buckets",
    "candidate_recall_at_k",
    "candidate_recall_at_pool",
    "classification_scores",
    "decision_policy_error_rate",
    "forbidden_candidate_violation_count",
    "forbidden_candidate_violation_rate",
    "mean_reciprocal_rank",
    "missed_clarification_rate",
    "ndcg_at_k",
    "no_match_false_positive_rate",
    "ranking_error_rate",
    "required_candidate_recall",
    "status_accuracy",
    "top_1_accepted_accuracy",
    "top_2_accepted_recall",
    "top_k_hit_rate",
    "unnecessary_clarification_rate",
    "unsafe_auto_select_count",
    "unsafe_auto_select_rate",
    "wilson_confidence_interval",
]
