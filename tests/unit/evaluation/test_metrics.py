"""Metrics correctness on hand-crafted predictions."""

from __future__ import annotations

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
from taksitlio.evaluation import metrics


def _case(
    cid: str,
    status: ExpectedStatus,
    *,
    required=(),
    acceptable=(),
    forbidden=(),
) -> EvaluationCase:
    return EvaluationCase(
        case_id=cid,
        utterance="x",
        locale="tr-TR",
        expected=CaseExpected(
            status=status,
            acceptable_fixture_keys=tuple(acceptable),
            required_fixture_keys=tuple(required),
            forbidden_fixture_keys=tuple(forbidden),
        ),
        dimensions=CaseDimensions(),
        privacy=CasePrivacy(),
        annotation=CaseAnnotation(status=AnnotationStatus.DRAFT),
    )


def _pred(
    cid: str,
    status: str,
    top_keys: list[tuple[str, float]],
    *,
    selected: str | None = None,
    latency_ms: float = 5.0,
) -> CasePrediction:
    top_k = tuple(
        CandidatePrediction(fixture_key=k, score=s, rank=i + 1)
        for i, (k, s) in enumerate(top_keys)
    )
    return CasePrediction(
        case_id=cid,
        predicted_status=status,
        selected_fixture_key=selected,
        top_k=top_k,
        latency_ms=latency_ms,
    )


def test_status_accuracy_counts_exact_match():
    cases = [
        _case("a", ExpectedStatus.MATCHED, acceptable=("fixture.mobile-device",)),
        _case("b", ExpectedStatus.NO_MATCH),
    ]
    preds = {
        "a": _pred("a", "MATCHED", [("fixture.mobile-device", 0.9)], selected="fixture.mobile-device"),
        "b": _pred("b", "NO_MATCH", []),
    }
    assert metrics.status_accuracy(cases, preds).value == 1.0


def test_unsafe_auto_select_flags_no_match_but_auto_selected():
    cases = [
        _case("x", ExpectedStatus.NO_MATCH),
        _case("y", ExpectedStatus.NO_MATCH),
    ]
    preds = {
        "x": _pred("x", "MATCHED", [("fixture.mobile-device", 0.8)], selected="fixture.mobile-device"),
        "y": _pred("y", "NO_MATCH", []),
    }
    assert metrics.unsafe_auto_select_rate(cases, preds).value == 0.5


def test_required_candidate_recall_requires_all_keys_present():
    cases = [
        _case(
            "a",
            ExpectedStatus.MATCHED,
            required=("fixture.mobile-device", "fixture.tablet-device"),
        ),
    ]
    preds_full = {
        "a": _pred(
            "a",
            "AMBIGUOUS",
            [
                ("fixture.mobile-device", 0.7),
                ("fixture.tablet-device", 0.65),
            ],
        )
    }
    preds_partial = {
        "a": _pred(
            "a",
            "AMBIGUOUS",
            [("fixture.mobile-device", 0.7)],
        )
    }
    assert metrics.required_candidate_recall(cases, preds_full).value == 1.0
    assert metrics.required_candidate_recall(cases, preds_partial).value == 0.0


def test_forbidden_candidate_violation_counts_presence_in_topk():
    cases = [
        _case(
            "z",
            ExpectedStatus.MATCHED,
            required=("fixture.portable-computer",),
            forbidden=("fixture.mobile-device",),
        )
    ]
    preds = {
        "z": _pred(
            "z",
            "MATCHED",
            [
                ("fixture.portable-computer", 0.9),
                ("fixture.mobile-device", 0.6),
            ],
            selected="fixture.portable-computer",
        )
    }
    assert metrics.forbidden_candidate_violation_rate(cases, preds).value == 1.0
    assert metrics.forbidden_candidate_violation_count(cases, preds) == 1


def test_error_buckets_partition_predictions():
    cases = [
        _case("m", ExpectedStatus.MATCHED, acceptable=("fixture.mobile-device",)),
        _case("n", ExpectedStatus.NO_MATCH),
        _case("a", ExpectedStatus.AMBIGUOUS),
    ]
    preds = {
        "m": _pred("m", "NO_MATCH", []),
        "n": _pred("n", "MATCHED", [("fixture.mobile-device", 0.9)], selected="fixture.mobile-device"),
        "a": _pred("a", "MATCHED", [("fixture.mobile-device", 0.9)], selected="fixture.mobile-device"),
    }
    buckets = metrics.build_error_buckets(cases, preds)
    assert buckets["no_match_when_should_match"].count == 1
    assert buckets["matched_when_should_be_no_match"].count == 1
    assert buckets["matched_when_should_be_ambiguous"].count == 1


def test_mrr_uses_first_acceptable_rank():
    cases = [
        _case(
            "one",
            ExpectedStatus.MATCHED,
            acceptable=("fixture.mobile-device",),
        )
    ]
    preds = {
        "one": _pred(
            "one",
            "AMBIGUOUS",
            [
                ("fixture.other-thing", 0.9),
                ("fixture.mobile-device", 0.6),
            ],
        )
    }
    assert metrics.mean_reciprocal_rank(cases, preds) == 0.5


def test_unnecessary_and_missed_clarification_rates():
    cases = [
        _case("a", ExpectedStatus.MATCHED, acceptable=("fixture.mobile-device",)),
        _case("b", ExpectedStatus.AMBIGUOUS),
    ]
    preds = {
        "a": _pred("a", "AMBIGUOUS", [("fixture.mobile-device", 0.6)]),
        "b": _pred("b", "MATCHED", [("fixture.mobile-device", 0.9)]),
    }
    assert metrics.unnecessary_clarification_rate(cases, preds).value == 1.0
    assert metrics.missed_clarification_rate(cases, preds).value == 1.0
