"""Compare real-runtime quality against test-double baseline (ADR-009 §9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RuntimeQualityComparison:
    baseline: Mapping[str, Any]
    runtime: Mapping[str, Any]
    deltas: Mapping[str, Optional[float]]
    safety_regression: bool
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": dict(self.baseline),
            "runtime": dict(self.runtime),
            "deltas": dict(self.deltas),
            "safety_regression": self.safety_regression,
            "notes": list(self.notes),
        }


_SAFETY_KEYS = ("forbidden", "unsafe", "forbidden_count", "unsafe_count")


def _num(payload: Mapping[str, Any], key: str) -> Optional[float]:
    if key not in payload:
        return None
    val = payload[key]
    if isinstance(val, Mapping) and "value" in val:
        return float(val["value"])
    if val is None:
        return None
    return float(val)


def compare_to_baseline(
    baseline: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    keys: tuple[str, ...] = (
        "status_accuracy",
        "top_1",
        "top_2",
        "required_recall",
        "pool_recall",
        "decision_policy_error",
        "unnecessary_clarification",
        "missed_clarification",
    ),
) -> RuntimeQualityComparison:
    deltas: dict[str, Optional[float]] = {}
    notes: list[str] = []
    for key in keys:
        b = _num(baseline, key)
        r = _num(runtime, key)
        if b is None or r is None:
            deltas[key] = None
            notes.append(f"{key} incomplete for comparison")
        else:
            deltas[key] = r - b

    safety_regression = False
    for key in _SAFETY_KEYS:
        b = _num(baseline, key)
        r = _num(runtime, key)
        if b is None or r is None:
            continue
        if r > b + 1e-12:
            safety_regression = True
            notes.append(f"safety regression on {key}: {b} → {r}")

    # Absolute hard floor: any non-zero forbidden/unsafe on runtime fails.
    for key in ("forbidden", "forbidden_count"):
        r = _num(runtime, key)
        if r is not None and r > 0:
            safety_regression = True
            notes.append(f"runtime {key}={r} > 0")
    for key in ("unsafe", "unsafe_count"):
        r = _num(runtime, key)
        if r is not None and r > 0:
            safety_regression = True
            notes.append(f"runtime {key}={r} > 0")

    return RuntimeQualityComparison(
        baseline=baseline,
        runtime=runtime,
        deltas=deltas,
        safety_regression=safety_regression,
        notes=tuple(notes),
    )
