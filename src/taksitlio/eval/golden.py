"""Turkish golden evaluation dataset loader + metrics harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class GoldenCase:
    id: str
    bucket: str
    message: str
    expected: dict[str, Any]


def default_dataset_path() -> Path:
    return Path(__file__).resolve().parents[3] / "eval" / "golden" / "tr_need_understanding.jsonl"


def load_golden_cases(path: Path | None = None) -> list[GoldenCase]:
    path = path or default_dataset_path()
    cases: list[GoldenCase] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cases.append(
                GoldenCase(
                    id=str(row["id"]),
                    bucket=str(row.get("bucket") or "unspecified"),
                    message=str(row["message"]),
                    expected=dict(row.get("expected") or {}),
                )
            )
    return cases


def intent_accuracy(cases: Sequence[GoldenCase], predictions: Mapping[str, Mapping[str, Any]]) -> float:
    return _field_accuracy(cases, predictions, path=("intent", "type"))


def budget_value_accuracy(
    cases: Sequence[GoldenCase],
    predictions: Mapping[str, Mapping[str, Any]],
    *,
    tolerance: float = 0.05,
) -> float:
    scored = 0
    total = 0
    for case in cases:
        exp = case.expected.get("budget") or {}
        exp_val = exp.get("value")
        if exp_val is None:
            continue
        total += 1
        pred = predictions.get(case.id) or {}
        got = (pred.get("budget") or {}).get("value")
        if got is None:
            continue
        if abs(float(got) - float(exp_val)) <= float(exp_val) * tolerance:
            scored += 1
    return scored / total if total else 0.0


def valid_json_rate(predictions: Iterable[Mapping[str, Any] | None]) -> float:
    items = list(predictions)
    if not items:
        return 0.0
    return sum(1 for p in items if isinstance(p, dict)) / len(items)


def summarize_buckets(cases: Sequence[GoldenCase]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.bucket] = counts.get(case.bucket, 0) + 1
    return counts


def _field_accuracy(
    cases: Sequence[GoldenCase],
    predictions: Mapping[str, Mapping[str, Any]],
    *,
    path: tuple[str, ...],
) -> float:
    scored = 0
    total = 0
    for case in cases:
        expected = _dig(case.expected, path)
        if expected is None:
            continue
        total += 1
        pred = predictions.get(case.id)
        if pred is None:
            continue
        if _dig(pred, path) == expected:
            scored += 1
    return scored / total if total else 0.0


def _dig(data: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node
