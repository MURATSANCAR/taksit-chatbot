"""Compare two evaluation reports against configured tolerances.

Used by ``compare-runs`` CLI and the promotion workflow described in
``admin/specs/category-evaluation-admin-screens.md``. Comparison is
metric-based; category names never appear in the diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from taksitlio.evaluation.errors import BaselineComparisonError


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    baseline: float
    candidate: float
    delta: float
    tolerance: float
    within_tolerance: bool
    direction: str  # "higher_is_better" or "lower_is_better"


@dataclass(frozen=True)
class ComparisonResult:
    baseline_run: str
    candidate_run: str
    deltas: tuple[MetricDelta, ...]
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "baseline_run": self.baseline_run,
            "candidate_run": self.candidate_run,
            "regressions": list(self.regressions),
            "improvements": list(self.improvements),
            "deltas": [
                {
                    "metric": d.metric,
                    "baseline": d.baseline,
                    "candidate": d.candidate,
                    "delta": d.delta,
                    "tolerance": d.tolerance,
                    "within_tolerance": d.within_tolerance,
                    "direction": d.direction,
                }
                for d in self.deltas
            ],
        }


def _flatten(prefix: str, obj) -> dict[str, float]:
    """Flatten nested metric dict into ``matched.f1`` style keys."""
    if isinstance(obj, Mapping):
        out: dict[str, float] = {}
        for k, v in obj.items():
            child = f"{prefix}.{k}" if prefix else k
            out.update(_flatten(child, v))
        return out
    if isinstance(obj, (int, float)):
        return {prefix: float(obj)}
    return {}


def _tolerance_for(metric: str, tolerances: Mapping[str, Mapping]) -> tuple[float, str]:
    rule = tolerances.get(metric)
    if not rule:
        return 0.02, "higher_is_better"
    return float(rule.get("tolerance", 0.02)), str(rule.get("direction", "higher_is_better"))


def compare_reports(
    baseline: Mapping,
    candidate: Mapping,
    *,
    tolerances: Mapping[str, Mapping],
) -> ComparisonResult:
    if "metrics" not in baseline or "metrics" not in candidate:
        raise BaselineComparisonError("reports must include a 'metrics' object")
    base_flat = _flatten("", baseline["metrics"])
    cand_flat = _flatten("", candidate["metrics"])
    metrics = sorted(set(base_flat) | set(cand_flat))
    deltas: list[MetricDelta] = []
    regressions: list[str] = []
    improvements: list[str] = []
    for metric in metrics:
        b = base_flat.get(metric, 0.0)
        c = cand_flat.get(metric, 0.0)
        delta = c - b
        tol, direction = _tolerance_for(metric, tolerances)
        within = abs(delta) <= tol
        if not within:
            if direction == "lower_is_better":
                if delta > 0:
                    regressions.append(metric)
                else:
                    improvements.append(metric)
            else:
                if delta < 0:
                    regressions.append(metric)
                else:
                    improvements.append(metric)
        deltas.append(
            MetricDelta(
                metric=metric,
                baseline=b,
                candidate=c,
                delta=delta,
                tolerance=tol,
                within_tolerance=within,
                direction=direction,
            )
        )
    return ComparisonResult(
        baseline_run=str(baseline.get("run_id", "")),
        candidate_run=str(candidate.get("run_id", "")),
        deltas=tuple(deltas),
        regressions=tuple(regressions),
        improvements=tuple(improvements),
    )


__all__ = ["ComparisonResult", "MetricDelta", "compare_reports"]
