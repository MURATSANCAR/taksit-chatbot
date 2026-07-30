"""Report I/O.

Standard reports live under ``evaluation/reports/<run_id>.json`` and
are checked for raw-utterance leaks before writing. Debug logs (opt-in)
live under ``evaluation/private/`` and are excluded from Git.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping, Optional

import jsonschema

from taksitlio.evaluation.dataset import SCHEMA_DIR
from taksitlio.evaluation.domain import CasePrediction, EvaluationCase
from taksitlio.evaluation.evaluator import EvaluationReport
from taksitlio.evaluation.privacy import (
    PRIVATE_DIR,
    REPORTS_DIR,
    assert_report_is_safe,
    utterance_hash,
)


def _load_schema() -> dict:
    with (SCHEMA_DIR / "evaluation_report.schema.json").open(encoding="utf-8") as fh:
        return json.load(fh)


_REPORT_VALIDATOR = jsonschema.Draft7Validator(_load_schema())


def write_report(
    report: EvaluationReport,
    *,
    reports_dir: Path = REPORTS_DIR,
) -> Path:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    assert_report_is_safe(payload)
    _REPORT_VALIDATOR.validate(payload)
    out = reports_dir / f"{report.run_id}.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return out


def write_debug_log(
    run_id: str,
    cases: Iterable[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
    *,
    private_dir: Path = PRIVATE_DIR,
) -> Path:
    """Opt-in debug log — contains hashed utterance ids + predictions.

    The file is stored under ``evaluation/private/`` and is gitignored.
    It does not contain raw utterances (ADR-005 §9).
    """
    private_dir = Path(private_dir)
    private_dir.mkdir(parents=True, exist_ok=True)
    out = private_dir / f"{run_id}-debug.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for case in cases:
            pred = predictions.get(case.case_id)
            payload = {
                "case_id": case.case_id,
                "semantic_group_id": case.semantic_group_id,
                "utterance_hash": utterance_hash(case.utterance),
                "expected_status": case.expected.status.value,
                "predicted_status": (pred.predicted_status if pred else None),
                "selected_fixture_key": (pred.selected_fixture_key if pred else None),
                "latency_ms": (pred.latency_ms if pred else None),
                "top_k": (
                    [
                        {
                            "fixture_key": c.fixture_key,
                            "score": c.score,
                            "rank": c.rank,
                            "alias_mode": c.alias_mode,
                        }
                        for c in (pred.top_k if pred else ())
                    ]
                ),
            }
            fh.write(json.dumps(payload, ensure_ascii=False))
            fh.write("\n")
    return out


def load_report(path: Path) -> dict:
    with Path(path).open(encoding="utf-8") as fh:
        return json.load(fh)


__all__ = ["load_report", "write_debug_log", "write_report"]
