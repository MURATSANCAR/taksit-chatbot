"""Unit tests for :mod:`taksitlio.evaluation.metrics` ProportionMetric shape.

ADR-006 §A: every rate metric must expose numerator, denominator, support
status and a Wilson score 95% confidence interval; a denominator of zero
must produce ``value=None`` (``NOT_APPLICABLE``) — never a fake 0.0 rate.
"""

from __future__ import annotations

import math

import pytest

from taksitlio.evaluation import metrics
from taksitlio.evaluation.domain import (
    AnnotationStatus,
    CandidatePrediction,
    CaseAnnotation,
    CaseDimensions,
    CaseExpected,
    CasePrediction,
    CasePrivacy,
    EvaluationCase,
    ExpectedStatus,
)
from taksitlio.evaluation.metrics import ProportionMetric


def _case(
    case_id: str,
    status: ExpectedStatus,
    *,
    acceptable: tuple[str, ...] = (),
    required: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        utterance="…",
        locale="tr-TR",
        expected=CaseExpected(
            status=status,
            acceptable_fixture_keys=acceptable,
            required_fixture_keys=required,
            forbidden_fixture_keys=forbidden,
        ),
        dimensions=CaseDimensions(),
        privacy=CasePrivacy(),
        annotation=CaseAnnotation(status=AnnotationStatus.DRAFT),
    )


def _pred(
    case_id: str,
    predicted_status: str,
    *,
    top_k: tuple[tuple[str, float], ...] = (),
    selected: str | None = None,
) -> CasePrediction:
    return CasePrediction(
        case_id=case_id,
        predicted_status=predicted_status,
        selected_fixture_key=selected,
        top_k=tuple(
            CandidatePrediction(fixture_key=k, score=s, rank=i + 1)
            for i, (k, s) in enumerate(top_k)
        ),
        latency_ms=1.0,
    )


def test_wilson_ci_zero_and_one_are_bounded() -> None:
    """Wilson interval must stay within [0, 1] at the extremes."""

    lo_hi = metrics.wilson_confidence_interval(0, 10)
    assert lo_hi is not None
    assert 0.0 <= lo_hi["lower"] <= lo_hi["upper"] <= 1.0
    perfect = metrics.wilson_confidence_interval(10, 10)
    assert perfect is not None
    assert perfect["upper"] == pytest.approx(1.0, rel=0, abs=1e-9)
    assert perfect["lower"] > 0.0
    assert metrics.wilson_confidence_interval(0, 0) is None


def test_status_accuracy_returns_proportion_metric() -> None:
    cases = [_case("c1", ExpectedStatus.MATCHED, acceptable=("k1",))]
    preds = {
        "c1": _pred("c1", "MATCHED", top_k=(("k1", 0.9),), selected="k1"),
    }
    result = metrics.status_accuracy(cases, preds)
    assert isinstance(result, ProportionMetric)
    assert result.metric == "status_accuracy"
    assert result.value == pytest.approx(1.0)
    assert result.numerator == 1
    assert result.denominator == 1
    assert result.support == 1
    assert result.support_status in {"OK", "LOW_SUPPORT"}
    assert result.confidence_interval_95 is not None


def test_empty_denominator_returns_not_applicable() -> None:
    """No forbidden keys -> denominator 0 -> value must be None."""

    cases = [_case("c1", ExpectedStatus.MATCHED, acceptable=("k1",))]
    preds = {"c1": _pred("c1", "MATCHED", top_k=(("k1", 0.9),), selected="k1")}
    rate = metrics.forbidden_candidate_violation_rate(cases, preds)
    assert rate.value is None
    assert rate.support_status == "NOT_APPLICABLE"
    assert rate.numerator == 0
    assert rate.denominator == 0
    assert rate.confidence_interval_95 is None
    # __float__ must still work — degrades to 0.0.
    assert float(rate) == 0.0


def test_forbidden_violation_count_is_integer() -> None:
    cases = [
        _case(
            "c1",
            ExpectedStatus.MATCHED,
            acceptable=("k1",),
            forbidden=("bad",),
        )
    ]
    preds = {
        "c1": _pred(
            "c1", "MATCHED", top_k=(("k1", 0.7), ("bad", 0.5)), selected="k1"
        )
    }
    count = metrics.forbidden_candidate_violation_count(cases, preds)
    assert count == 1


def test_ranking_error_rate_denominator_uses_pool_hit_only() -> None:
    cases = [_case("c1", ExpectedStatus.MATCHED, acceptable=("k1",))]
    # Acceptable key IS in pool (pool_fixture_keys) but NOT in top_2 (top_k).
    pred = CasePrediction(
        case_id="c1",
        predicted_status="MATCHED",
        selected_fixture_key="other",
        top_k=(
            CandidatePrediction(fixture_key="x", score=0.9, rank=1),
            CandidatePrediction(fixture_key="y", score=0.7, rank=2),
        ),
        latency_ms=1.0,
        pool_fixture_keys=("k1", "x", "y"),
    )
    ranking = metrics.ranking_error_rate(cases, {"c1": pred})
    assert isinstance(ranking, ProportionMetric)
    assert ranking.value == pytest.approx(1.0)
    assert ranking.numerator == 1
    assert ranking.denominator == 1


def test_candidate_recall_at_pool_uses_pool_union() -> None:
    cases = [_case("c1", ExpectedStatus.MATCHED, acceptable=("k1",))]
    pred = CasePrediction(
        case_id="c1",
        predicted_status="AMBIGUOUS",
        selected_fixture_key=None,
        top_k=(
            CandidatePrediction(fixture_key="x", score=0.5, rank=1),
        ),
        latency_ms=1.0,
        pool_fixture_keys=("k1", "x"),
    )
    pool_recall = metrics.candidate_recall_at_pool(cases, {"c1": pred})
    assert pool_recall.value == pytest.approx(1.0)
    assert pool_recall.numerator == 1
