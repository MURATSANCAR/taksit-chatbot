"""Quality-gate driven acceptance/rejection.

Verifies the evaluator refuses to ACCEPT a run whose unsafe auto-select
rate crosses the configured threshold, even when overall status
accuracy is respectable — ADR-005 §7.
"""

from __future__ import annotations

from taksitlio.evaluation.domain import (
    AnnotationStatus,
    CandidatePrediction,
    CaseAnnotation,
    CaseDimensions,
    CaseExpected,
    CasePrediction,
    CasePrivacy,
    DatasetSplit,
    EvaluationCase,
    EvaluationDataset,
    EvaluationMode,
    ExpectedStatus,
    QualityGateStatus,
)
from taksitlio.evaluation.evaluator import evaluate


def _case(cid: str, status: ExpectedStatus, key: str = "fixture.mobile-device") -> EvaluationCase:
    return EvaluationCase(
        case_id=cid,
        utterance="x",
        locale="tr-TR",
        expected=CaseExpected(
            status=status,
            acceptable_fixture_keys=(key,) if status is ExpectedStatus.MATCHED else (),
            required_fixture_keys=(key,) if status is ExpectedStatus.MATCHED else (),
        ),
        dimensions=CaseDimensions(),
        privacy=CasePrivacy(),
        annotation=CaseAnnotation(status=AnnotationStatus.DRAFT),
    )


def test_unsafe_auto_select_forces_reject():
    cases = [
        _case("m1", ExpectedStatus.MATCHED),
        _case("m2", ExpectedStatus.MATCHED),
        _case("n1", ExpectedStatus.NO_MATCH),
        _case("n2", ExpectedStatus.NO_MATCH),
    ]
    predictions = {
        "m1": CasePrediction(
            case_id="m1",
            predicted_status="MATCHED",
            selected_fixture_key="fixture.mobile-device",
            top_k=(CandidatePrediction("fixture.mobile-device", 0.9, 1),),
            latency_ms=5.0,
        ),
        "m2": CasePrediction(
            case_id="m2",
            predicted_status="MATCHED",
            selected_fixture_key="fixture.mobile-device",
            top_k=(CandidatePrediction("fixture.mobile-device", 0.9, 1),),
            latency_ms=5.0,
        ),
        # Unsafe: NO_MATCH ground truth but matcher auto-selected
        "n1": CasePrediction(
            case_id="n1",
            predicted_status="MATCHED",
            selected_fixture_key="fixture.mobile-device",
            top_k=(CandidatePrediction("fixture.mobile-device", 0.9, 1),),
            latency_ms=5.0,
        ),
        "n2": CasePrediction(
            case_id="n2",
            predicted_status="MATCHED",
            selected_fixture_key="fixture.mobile-device",
            top_k=(CandidatePrediction("fixture.mobile-device", 0.9, 1),),
            latency_ms=5.0,
        ),
    }
    dataset = EvaluationDataset(
        dataset_id="mini",
        version="v1",
        split=DatasetSplit.VALIDATION,
        fixture_catalog_ref={"catalog_id": "fixture.category-catalog", "version": "v1"},
        cases=tuple(cases),
    )
    config = {
        "latency_budget_p95_ms": 5000,
        "objective_weights": {"status_accuracy": 1.0, "unsafe_auto_select_rate": -1.0},
        "quality_gate_thresholds": {
            "unsafe_auto_select_rate": {"max": 0.10},
        },
    }
    report = evaluate(
        dataset,
        predictions,
        mode=EvaluationMode.FULL,
        policy={"policy_code": "CATEGORY_MATCH_DEFAULT"},
        config=config,
        latency_values=[5.0, 5.0, 5.0, 5.0],
        concurrency={"workers": 1, "queue_wait_p95_ms": 0.0, "throughput_qps": 200.0},
    )
    # DRAFT synthetic dataset cannot ACCEPT even if gate passes; gate failure
    # here is exposed as either REJECT or INSUFFICIENT_REVIEWED_DATA.
    assert report.quality_gate["status"] in {
        QualityGateStatus.REJECT.value,
        QualityGateStatus.INSUFFICIENT_REVIEWED_DATA.value,
    }
    assert report.quality_gate["status"] != QualityGateStatus.ACCEPT.value
    assert report.quality_gate["status"] != QualityGateStatus.PROVISIONAL_ACCEPT.value
    assert any("unsafe_auto_select_rate" in v for v in report.quality_gate["violations"])
