"""Dataset parsing + schema validation.

JSONL semantics: one case per line, deserialized against the
``category_match_case.schema.json`` schema. Datasets are loaded from
``evaluation/datasets/<split>/<name>.vN.jsonl`` — the file path
determines both the split and dataset identity, so we never encode
category names or business content into runtime code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

import jsonschema

from taksitlio.evaluation.domain import (
    AnnotationStatus,
    CaseAnnotation,
    CaseDimensions,
    CaseExpected,
    CasePrivacy,
    DatasetSplit,
    EvaluationCase,
    EvaluationDataset,
    ExpectedStatus,
)
from taksitlio.evaluation.errors import DatasetValidationError


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "schemas"


def _load_schema() -> dict:
    path = SCHEMA_DIR / "category_match_case.schema.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


_CASE_SCHEMA = _load_schema()
_CASE_VALIDATOR = jsonschema.Draft7Validator(_CASE_SCHEMA)


def _validate_case(payload: dict, *, line_no: int, issues: list[str]) -> None:
    errors = sorted(_CASE_VALIDATOR.iter_errors(payload), key=lambda e: e.path)
    for err in errors:
        loc = "/".join(str(p) for p in err.path) or "<root>"
        issues.append(f"line {line_no}: {loc}: {err.message}")


def _case_from_payload(payload: dict) -> EvaluationCase:
    expected = payload["expected"]
    dims = payload.get("dimensions") or {}
    privacy = payload.get("privacy") or {"synthetic": True}
    annotation = payload["annotation"]
    return EvaluationCase(
        case_id=str(payload["case_id"]),
        utterance=str(payload["utterance"]),
        locale=str(payload["locale"]),
        semantic_group_id=payload.get("semantic_group_id"),
        hints=tuple(payload.get("hints") or ()),
        expected=CaseExpected(
            status=ExpectedStatus(expected["status"]),
            acceptable_fixture_keys=tuple(
                expected.get("acceptable_fixture_keys") or ()
            ),
            required_fixture_keys=tuple(
                expected.get("required_fixture_keys") or ()
            ),
            forbidden_fixture_keys=tuple(
                expected.get("forbidden_fixture_keys") or ()
            ),
            expected_confidence_bucket=expected.get("expected_confidence_bucket"),
        ),
        dimensions=CaseDimensions(
            tags=tuple(dims.get("tags") or ()),
            difficulty=dims.get("difficulty"),
        ),
        privacy=CasePrivacy(
            synthetic=bool(privacy.get("synthetic", True)),
            contains_pii=bool(privacy.get("contains_pii", False)),
            source=privacy.get("source"),
        ),
        annotation=CaseAnnotation(
            status=AnnotationStatus(annotation["status"]),
            reviewers=tuple(annotation.get("reviewers") or ()),
            notes_hash=annotation.get("notes_hash"),
        ),
    )


def split_from_path(path: Path) -> DatasetSplit:
    parts = path.resolve().parts
    for part in reversed(parts):
        if part == "development":
            return DatasetSplit.DEVELOPMENT
        if part == "validation":
            return DatasetSplit.VALIDATION
        if part == "holdout":
            return DatasetSplit.HOLDOUT
    raise DatasetValidationError(
        f"cannot infer split from path {path}",
        issues=[f"path {path} not under development/, validation/, or holdout/"],
    )


def dataset_id_from_path(path: Path) -> tuple[str, str]:
    stem = path.stem  # e.g. tr-category-dev.v1
    parts = stem.rsplit(".", 1)
    if len(parts) != 2 or not parts[1].startswith("v"):
        raise DatasetValidationError(
            f"dataset filename must end with .vN (got {path.name})"
        )
    return parts[0], parts[1]


def load_jsonl(
    path: Path,
    *,
    validate_schema: bool = True,
    fixture_catalog_ref: Optional[dict] = None,
) -> EvaluationDataset:
    path = Path(path)
    if not path.exists():
        raise DatasetValidationError(
            f"dataset file not found: {path}",
            issues=[f"missing file {path}"],
        )
    split = split_from_path(path)
    dataset_id, version = dataset_id_from_path(path)

    issues: list[str] = []
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    hasher = hashlib.sha256()

    with path.open(encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            hasher.update(stripped.encode("utf-8"))
            hasher.update(b"\n")
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                issues.append(f"line {line_no}: invalid JSON — {exc}")
                continue
            if validate_schema:
                _validate_case(payload, line_no=line_no, issues=issues)
                if issues and issues[-1].startswith(f"line {line_no}:"):
                    continue
            case = _case_from_payload(payload)
            if case.case_id in seen_ids:
                issues.append(f"line {line_no}: duplicate case_id {case.case_id}")
                continue
            seen_ids.add(case.case_id)
            cases.append(case)

    if issues:
        raise DatasetValidationError(
            f"dataset {path.name} failed validation ({len(issues)} issue(s))",
            issues=issues,
        )

    dataset_ref = fixture_catalog_ref or {
        "catalog_id": "fixture.category-catalog",
        "version": "v1",
    }
    return EvaluationDataset(
        dataset_id=dataset_id,
        version=version,
        split=split,
        fixture_catalog_ref=dataset_ref,
        cases=tuple(cases),
        immutable_hash=hasher.hexdigest(),
    )


def write_jsonl(path: Path, cases: Iterable[EvaluationCase]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(_case_to_payload(case), ensure_ascii=False))
            fh.write("\n")
    return path


def _case_to_payload(case: EvaluationCase) -> dict:
    payload: dict = {
        "case_id": case.case_id,
        "utterance": case.utterance,
        "locale": case.locale,
        "expected": {
            "status": case.expected.status.value,
        },
        "dimensions": {
            "tags": list(case.dimensions.tags),
        },
        "privacy": {
            "synthetic": case.privacy.synthetic,
        },
        "annotation": {
            "status": case.annotation.status.value,
        },
    }
    if case.semantic_group_id:
        payload["semantic_group_id"] = case.semantic_group_id
    if case.hints:
        payload["hints"] = list(case.hints)
    exp = case.expected
    if exp.acceptable_fixture_keys:
        payload["expected"]["acceptable_fixture_keys"] = list(exp.acceptable_fixture_keys)
    if exp.required_fixture_keys:
        payload["expected"]["required_fixture_keys"] = list(exp.required_fixture_keys)
    if exp.forbidden_fixture_keys:
        payload["expected"]["forbidden_fixture_keys"] = list(exp.forbidden_fixture_keys)
    if exp.expected_confidence_bucket:
        payload["expected"]["expected_confidence_bucket"] = exp.expected_confidence_bucket
    if case.dimensions.difficulty:
        payload["dimensions"]["difficulty"] = case.dimensions.difficulty
    if case.privacy.contains_pii:
        payload["privacy"]["contains_pii"] = True
    if case.privacy.source:
        payload["privacy"]["source"] = case.privacy.source
    if case.annotation.reviewers:
        payload["annotation"]["reviewers"] = list(case.annotation.reviewers)
    if case.annotation.notes_hash:
        payload["annotation"]["notes_hash"] = case.annotation.notes_hash
    return payload


def assert_split_integrity(
    development: Sequence[EvaluationCase],
    validation: Sequence[EvaluationCase],
    holdout: Sequence[EvaluationCase],
) -> None:
    """Case_ids and semantic_group_ids must not overlap across splits.

    ADR-005 §6: tuning on the holdout is forbidden; that starts with
    keeping the split disjoint. Semantic group ids also cannot leak —
    otherwise a near-duplicate slips into the "unseen" split.
    """

    issues: list[str] = []
    dev_ids = {c.case_id for c in development}
    val_ids = {c.case_id for c in validation}
    hold_ids = {c.case_id for c in holdout}
    for a_name, a_ids, b_name, b_ids in (
        ("development", dev_ids, "validation", val_ids),
        ("development", dev_ids, "holdout", hold_ids),
        ("validation", val_ids, "holdout", hold_ids),
    ):
        overlap = a_ids & b_ids
        if overlap:
            issues.append(
                f"case_id overlap {a_name}↔{b_name}: {sorted(overlap)[:5]}"
            )
    def _groups(items: Sequence[EvaluationCase]) -> set:
        return {c.semantic_group_id for c in items if c.semantic_group_id}
    dev_g = _groups(development)
    val_g = _groups(validation)
    hold_g = _groups(holdout)
    for a_name, a_g, b_name, b_g in (
        ("development", dev_g, "validation", val_g),
        ("development", dev_g, "holdout", hold_g),
        ("validation", val_g, "holdout", hold_g),
    ):
        overlap = a_g & b_g
        if overlap:
            issues.append(
                f"semantic_group_id overlap {a_name}↔{b_name}: {sorted(overlap)[:5]}"
            )
    if issues:
        raise DatasetValidationError(
            "split integrity violated",
            issues=issues,
        )


__all__ = [
    "assert_split_integrity",
    "dataset_id_from_path",
    "load_jsonl",
    "split_from_path",
    "write_jsonl",
]
