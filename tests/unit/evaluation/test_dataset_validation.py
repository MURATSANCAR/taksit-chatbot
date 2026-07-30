"""Dataset schema + split-integrity validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taksitlio.evaluation.dataset import (
    assert_split_integrity,
    load_jsonl,
    split_from_path,
)
from taksitlio.evaluation.domain import (
    AnnotationStatus,
    CaseAnnotation,
    CaseDimensions,
    CaseExpected,
    CasePrivacy,
    DatasetSplit,
    EvaluationCase,
    ExpectedStatus,
)
from taksitlio.evaluation.errors import DatasetValidationError


ROOT = Path(__file__).resolve().parents[3]
DEV = ROOT / "evaluation" / "datasets" / "development" / "tr-category-dev.v1.jsonl"
VAL = ROOT / "evaluation" / "datasets" / "golden" / "tr-category-validation.v1.jsonl"
HOLD = ROOT / "evaluation" / "datasets" / "golden" / "tr-category-holdout.v1.jsonl"


def test_dev_dataset_meets_minimum_size():
    dataset = load_jsonl(DEV)
    assert dataset.split is DatasetSplit.DEVELOPMENT
    assert len(dataset.cases) >= 150


def test_validation_dataset_meets_minimum_size():
    dataset = load_jsonl(VAL)
    assert dataset.split is DatasetSplit.VALIDATION
    assert len(dataset.cases) >= 50


def test_holdout_dataset_meets_minimum_size():
    dataset = load_jsonl(HOLD)
    assert dataset.split is DatasetSplit.HOLDOUT
    assert len(dataset.cases) >= 50


def test_all_cases_are_synthetic_and_not_human_reviewed():
    for path in (DEV, VAL, HOLD):
        for case in load_jsonl(path).cases:
            assert case.privacy.synthetic is True
            assert case.annotation.status is not AnnotationStatus.HUMAN_REVIEWED


def test_case_fixture_keys_only_reference_fixture_namespace():
    for path in (DEV, VAL, HOLD):
        dataset = load_jsonl(path)
        for case in dataset.cases:
            for group in (
                case.expected.acceptable_fixture_keys,
                case.expected.required_fixture_keys,
                case.expected.forbidden_fixture_keys,
            ):
                for key in group:
                    assert key.startswith("fixture."), key


def test_split_integrity_between_generated_splits():
    dev = load_jsonl(DEV).cases
    val = load_jsonl(VAL).cases
    hold = load_jsonl(HOLD).cases
    assert_split_integrity(dev, val, hold)


def test_case_ids_are_unique_within_dataset():
    for path in (DEV, VAL, HOLD):
        dataset = load_jsonl(path)
        ids = [c.case_id for c in dataset.cases]
        assert len(ids) == len(set(ids)), path


def test_invalid_case_raises_dataset_validation_error(tmp_path):
    bad = tmp_path / "bad.v1.jsonl"
    bad.parent.mkdir(parents=True, exist_ok=True)
    # split cannot be inferred from tmp_path — path must expose split
    development = tmp_path / "development"
    development.mkdir()
    bad = development / "bad.v1.jsonl"
    bad.write_text(
        json.dumps({"case_id": "x!", "utterance": "", "locale": "tr-TR"})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetValidationError):
        load_jsonl(bad)


def test_split_integrity_rejects_overlapping_groups():
    def _case(cid: str, group: str) -> EvaluationCase:
        return EvaluationCase(
            case_id=cid,
            utterance="x",
            locale="tr-TR",
            semantic_group_id=group,
            expected=CaseExpected(status=ExpectedStatus.MATCHED),
            dimensions=CaseDimensions(),
            privacy=CasePrivacy(),
            annotation=CaseAnnotation(status=AnnotationStatus.DRAFT),
        )

    dev = [_case("d1", "shared")]
    val = [_case("v1", "shared")]
    hold = [_case("h1", "unique")]
    with pytest.raises(DatasetValidationError):
        assert_split_integrity(dev, val, hold)


def test_split_from_path_recognises_golden_filenames(tmp_path):
    golden = tmp_path / "golden"
    golden.mkdir()
    val_path = golden / "tr-category-validation.v1.jsonl"
    val_path.write_text("", encoding="utf-8")
    hold_path = golden / "tr-category-holdout.v1.jsonl"
    hold_path.write_text("", encoding="utf-8")
    assert split_from_path(val_path) is DatasetSplit.VALIDATION
    assert split_from_path(hold_path) is DatasetSplit.HOLDOUT
