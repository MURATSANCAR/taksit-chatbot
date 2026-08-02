"""Check plan dimensions against available catalog capabilities."""

from __future__ import annotations

from typing import Any, Optional

from taksitlio.query_planning.models import CanonicalSearchPlan, PlanItem

SUBJECTIVE_DIMENSIONS: frozenset[str] = frozenset({
    "quiet",
    "heat",
    "build_quality",
    "screen_quality",
    "comfort",
    "loudness",
    "noise_level",
    "durability",
    "ergonomics",
    "aesthetics",
    "feel",
    "smoothness",
    "taste",
    "smell",
})


def _check_item_capabilities(
    item: PlanItem,
    catalog_dimensions: set[str] | None,
) -> list[str]:
    """Return dimension names from the item that the catalog cannot support."""
    unsupported: list[str] = []
    all_constraints = item.hard_constraints + item.soft_preferences
    for c in all_constraints:
        dim = c.dimension
        if not dim:
            continue
        if dim in SUBJECTIVE_DIMENSIONS:
            if catalog_dimensions is None or dim not in catalog_dimensions:
                if dim not in unsupported:
                    unsupported.append(dim)
                continue
        if catalog_dimensions is not None and dim not in catalog_dimensions:
            if dim not in ("category", "brand", "budget", "merchant"):
                if dim not in unsupported:
                    unsupported.append(dim)
    return unsupported


def check_capabilities(
    plan: CanonicalSearchPlan,
    *,
    catalog_dimensions: set[str] | None = None,
    finance_ready: bool = True,
) -> CanonicalSearchPlan:
    """Update plan items with unsupported dimensions and plan-level unsupported capabilities."""
    plan_unsupported: list[str] = []

    for item in plan.items:
        item_unsupported = _check_item_capabilities(item, catalog_dimensions)
        item.unsupported_dimensions = item_unsupported
        for dim in item_unsupported:
            label = f"UNSUPPORTED_BY_CATALOG:{dim}"
            if label not in plan_unsupported:
                plan_unsupported.append(label)

    if not finance_ready:
        ci = plan.campaign_intent
        if ci and (ci.requested or ci.required):
            cap = "FINANCE_NOT_READY"
            if cap not in plan_unsupported:
                plan_unsupported.append(cap)

    plan.unsupported_capabilities = plan_unsupported
    return plan
