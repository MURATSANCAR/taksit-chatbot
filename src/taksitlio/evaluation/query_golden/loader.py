"""Load Query Golden Set JSONL cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QueryGoldenCase:
    case_id: str
    bucket: str
    message: str
    expected: dict[str, Any]
    dimensions: dict[str, Any]
    privacy: dict[str, Any]
    annotation: dict[str, Any]
    locale: str = "tr-TR"


def default_dataset_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "evaluation"
        / "datasets"
        / "query_golden"
        / "v1"
        / "query_golden.v1.jsonl"
    )


def load_query_golden_cases(path: Path | None = None) -> list[QueryGoldenCase]:
    path = path or default_dataset_path()
    cases: list[QueryGoldenCase] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cases.append(
                QueryGoldenCase(
                    case_id=str(row["case_id"]),
                    bucket=str(row["bucket"]),
                    message=str(row["message"]),
                    expected=dict(row.get("expected") or {}),
                    dimensions=dict(row.get("dimensions") or {}),
                    privacy=dict(row.get("privacy") or {}),
                    annotation=dict(row.get("annotation") or {}),
                    locale=str(row.get("locale") or "tr-TR"),
                )
            )
    return cases


def summarize_buckets(cases: list[QueryGoldenCase]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.bucket] = counts.get(case.bucket, 0) + 1
    return counts
