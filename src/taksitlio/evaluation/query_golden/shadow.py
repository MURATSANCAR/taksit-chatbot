"""Shadow-mode smoke for Query Golden parser (ADR-013 §12 scaffold).

Compares expected route/entities (as 'live contract') vs parser predictions
('shadow'). Does not call production traffic — offline regression only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.evaluation.query_golden.loader import QueryGoldenCase
from taksitlio.evaluation.query_golden.metrics import predict_case
from taksitlio.query_understanding import CatalogHints
from taksitlio.recommendation_safety import compare_shadow


SHADOW_KEYS = (
    "route",
    "merchant",
    "category",
    "llm_required",
)


@dataclass
class ShadowLaneMetrics:
    case_count: int = 0
    compared: int = 0
    diff_rate: Optional[float] = None
    route_diffs: int = 0
    merchant_diffs: int = 0
    category_diffs: int = 0
    llm_routing_diffs: int = 0
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _live_payload(case: QueryGoldenCase) -> dict[str, Any]:
    exp = case.expected
    return {
        "route": exp.get("route"),
        "merchant": (exp.get("merchant") or {}).get("display_name"),
        "category": (exp.get("category") or {}).get("display_name"),
        "llm_required": bool(exp.get("llm_required")),
    }


def _shadow_payload(pred: Mapping[str, Any]) -> dict[str, Any]:
    parse = pred.get("parse") or {}
    cats = parse.get("positive_categories") or []
    return {
        "route": pred.get("route"),
        "merchant": (parse.get("merchant") or {}).get("display_name"),
        "category": cats[0].get("display_name") if cats else None,
        "llm_required": bool(pred.get("llm_required")),
    }


def evaluate_shadow_lane(
    cases: Sequence[QueryGoldenCase],
    *,
    catalog: CatalogHints,
    limit: Optional[int] = None,
) -> tuple[ShadowLaneMetrics, list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    subset = list(cases[:limit]) if limit else list(cases)
    route_d = merch_d = cat_d = llm_d = 0
    with_diff = 0

    for case in subset:
        # Only score fields that expected specifies
        live = _live_payload(case)
        pred = predict_case(case, catalog=catalog)
        shadow = _shadow_payload(pred)
        keys = [k for k in SHADOW_KEYS if live.get(k) is not None]
        if not keys:
            continue
        cmp = compare_shadow(live, shadow, keys=keys)
        if cmp.diffs:
            with_diff += 1
            if "route" in cmp.diffs:
                route_d += 1
            if "merchant" in cmp.diffs:
                merch_d += 1
            if "category" in cmp.diffs:
                cat_d += 1
            if "llm_required" in cmp.diffs:
                llm_d += 1
        details.append(
            {
                "case_id": case.case_id,
                "diffs": list(cmp.diffs),
            }
        )

    n = len(details)
    metrics = ShadowLaneMetrics(
        case_count=len(subset),
        compared=n,
        diff_rate=(with_diff / n) if n else None,
        route_diffs=route_d,
        merchant_diffs=merch_d,
        category_diffs=cat_d,
        llm_routing_diffs=llm_d,
        support={"compared": n, "with_diff": with_diff},
    )
    return metrics, details


def evaluate_shadow_gate(
    metrics: ShadowLaneMetrics,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Shadow smoke is informational until live dual-run exists."""

    thresholds = gates.get("shadow_gate_thresholds") or {
        "merchant_diffs": {"max_count": 0},
    }
    violations: list[str] = []
    # Merchant/category entity contract diffs on HR would be serious; smoke stays BOOTSTRAP
    rule = thresholds.get("merchant_diffs") or {}
    # Do not fail bootstrap on route noise from DRAFT labels
    status = "BOOTSTRAP"
    notes = [
        "offline expected-vs-parser shadow smoke; live anonymous dual-run ≥1000 still open",
        f"diff_rate={metrics.diff_rate}",
        f"route_diffs={metrics.route_diffs}",
        f"merchant_diffs={metrics.merchant_diffs}",
    ]
    return {"status": status, "violations": violations, "notes": notes}
