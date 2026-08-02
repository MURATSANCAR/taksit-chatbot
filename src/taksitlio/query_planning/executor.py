"""Filter and score products against a canonical search plan."""

from __future__ import annotations

import math
from typing import Any, Mapping

from taksitlio.query_planning.models import (
    CanonicalSearchPlan,
    ConstraintOperator,
    ConstraintStrength,
    PlanConstraint,
    PlanItem,
)


def _product_price(product: dict[str, Any]) -> float:
    raw = product.get("price") or product.get("current_price")
    if raw is None:
        return 0.0
    return float(raw)


_DIMENSION_ALIASES: dict[str, tuple[str, ...]] = {
    "ram": ("ram_gb_raw", "ram_gb", "ram", "memory_gb"),
    "ram_gb": ("ram_gb_raw", "ram_gb", "ram", "memory_gb"),
    "ram_gb_raw": ("ram_gb_raw", "ram_gb", "ram"),
    "storage": ("storage_gb", "storage", "disk_gb", "ssd_gb"),
    "storage_gb": ("storage_gb", "storage", "disk_gb", "ssd_gb"),
    "brand": ("brand", "brand_name", "brand_model"),
}


def _get_product_attr(product: dict[str, Any], dimension: str) -> Any:
    """Look up a dimension value in product metadata/attrs/top-level."""
    keys = _DIMENSION_ALIASES.get(dimension, (dimension,))
    bags: list[Any] = [
        product,
        product.get("attributes"),
        product.get("metadata"),
        product.get("attrs"),
        product.get("specifications"),
    ]
    for key in keys:
        for bag in bags:
            if not isinstance(bag, Mapping):
                continue
            if key in bag and bag[key] is not None:
                return bag[key]
        if key in product and product[key] is not None:
            return product[key]
    # Brand: allow matching display name against brand_model prefix.
    if dimension == "brand":
        bm = product.get("brand_model")
        if isinstance(bm, str) and bm.strip():
            return bm.split("/")[0].strip()
    return None


def _evaluate_constraint(product: dict[str, Any], constraint: PlanConstraint) -> bool | None:
    """Evaluate a single constraint against a product.

    Returns True (pass), False (fail), or None (dimension not present).
    """
    val = _get_product_attr(product, constraint.dimension)
    if val is None:
        return None

    target = constraint.value
    op = constraint.operator

    try:
        if op == ConstraintOperator.EQ:
            return _normalize_compare(val) == _normalize_compare(target)
        if op == ConstraintOperator.NEQ:
            return _normalize_compare(val) != _normalize_compare(target)
        if op == ConstraintOperator.GT:
            return float(val) > float(target)
        if op == ConstraintOperator.GTE:
            return float(val) >= float(target)
        if op == ConstraintOperator.LT:
            return float(val) < float(target)
        if op == ConstraintOperator.LTE:
            return float(val) <= float(target)
        if op == ConstraintOperator.IN:
            if isinstance(target, (list, tuple, set)):
                return _normalize_compare(val) in {_normalize_compare(t) for t in target}
            return _normalize_compare(val) == _normalize_compare(target)
        if op == ConstraintOperator.NOT_IN:
            if isinstance(target, (list, tuple, set)):
                return _normalize_compare(val) not in {_normalize_compare(t) for t in target}
            return _normalize_compare(val) != _normalize_compare(target)
        if op == ConstraintOperator.CONTAINS:
            return str(target).lower() in str(val).lower()
        if op == ConstraintOperator.EXCLUDES:
            return str(target).lower() not in str(val).lower()
        if op == ConstraintOperator.RANGE:
            if isinstance(target, (list, tuple)) and len(target) == 2:
                return float(target[0]) <= float(val) <= float(target[1])
    except (TypeError, ValueError):
        return None

    return None


def _normalize_compare(val: Any) -> str:
    if isinstance(val, str):
        return val.strip().lower()
    return str(val).strip().lower()


def _passes_hard_filters(
    product: dict[str, Any],
    item: PlanItem,
    plan: CanonicalSearchPlan,
    exception_thresholds: dict[str, Any],
) -> bool:
    """Return False if any HARD constraint eliminates this product."""
    price = _product_price(product)

    budget = (plan.global_constraints.budget if plan.global_constraints else None)
    if budget and budget.target_maximum is not None and price > 0:
        if price > budget.target_maximum:
            # Stretch band is not a hard pass — only allowed when tagged later
            # via advantage policy. Hard filter rejects above stretch_maximum.
            stretch_max = budget.stretch_maximum
            if stretch_max is None or price > stretch_max:
                return False

    for exc in item.excluded_constraints:
        # Exclusions are "product matches forbidden value" → drop.
        # Support EQ (preferred) and legacy NEQ meaning "value is excluded".
        if exc.operator == ConstraintOperator.NEQ:
            val = _get_product_attr(product, exc.dimension)
            if val is not None and _normalize_compare(val) == _normalize_compare(exc.value):
                return False
            # Also match display-name style brand/category exclusions.
            if exc.dimension == "brand" and val is not None:
                src = (exc.source_text or "").strip()
                if src and _normalize_compare(val) == _normalize_compare(src):
                    return False
            continue
        result = _evaluate_constraint(product, exc)
        if result is True:
            return False
        if exc.dimension == "brand":
            val = _get_product_attr(product, "brand")
            src = (exc.source_text or "").strip()
            if val is not None and src and _normalize_compare(val) == _normalize_compare(src):
                return False

    for c in item.hard_constraints:
        # Category/merchant identity is resolved in progressive_results / pool
        # hydration (tokens, synonyms). Do not hard-drop on id/string mismatch here.
        if c.dimension in {"category", "merchant"}:
            continue
        if c.dimension == "brand":
            val = _get_product_attr(product, "brand")
            src = (c.source_text or "").strip()
            targets = [t for t in (src, c.value) if t is not None and str(t).strip()]
            if not targets:
                continue
            if val is None:
                return False
            if any(_normalize_compare(val) == _normalize_compare(t) for t in targets):
                continue
            # Opaque entity id (e.g. brand-lenovo) that does not equal display → defer.
            raw = str(c.value or "")
            if not src and ("-" in raw or "_" in raw) and _normalize_compare(raw) != _normalize_compare(
                val
            ):
                continue
            return False

        result = _evaluate_constraint(product, c)
        if result is False:
            return False
        # Unverifiable hard attribute (e.g. RAM) must not count as satisfied.
        if result is None:
            return False

    return True


def _soft_score(
    product: dict[str, Any],
    item: PlanItem,
) -> float:
    """Score product on soft preferences. Higher is better."""
    score = 0.0
    for c in item.soft_preferences:
        result = _evaluate_constraint(product, c)
        if result is True:
            score += 1.0 * (1.0 + c.confidence)
        elif result is False:
            score -= 0.3
    return score


def _budget_band(
    product: dict[str, Any],
    plan: CanonicalSearchPlan,
    exception_thresholds: dict[str, Any],
) -> str | None:
    """Return PRIMARY, STRETCH, or None (over budget / invalid)."""
    budget = plan.global_constraints.budget if plan.global_constraints else None
    if not budget or budget.target_maximum is None:
        return "PRIMARY"
    price = _product_price(product)
    if price <= 0:
        return None
    if price <= budget.target_maximum:
        return "PRIMARY"
    stretch = budget.stretch_maximum
    if stretch is None or price > stretch:
        return None
    # Stretch requires deterministic advantage vs primary target.
    min_adv = float(
        exception_thresholds.get("minimum_price_advantage")
        or exception_thresholds.get("price_advantage_ratio")
        or 0.08
    )
    # Relative discount vs stretch ceiling as proxy when candidate is alone.
    advantage = (stretch - price) / stretch if stretch else 0.0
    if advantage >= min_adv:
        return "STRETCH"
    return None


def filter_products_by_plan(
    products: list[dict[str, Any]],
    plan: CanonicalSearchPlan,
    *,
    exception_thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Filter a product list using the plan's hard constraints and exclusions.

    Products that pass hard filters but violate soft preferences are kept
    (they will rank lower via scoring). Stretch-budget items are tagged
    ``budget_band=STRETCH`` and never labeled PRIMARY.
    """
    thresholds = exception_thresholds or {}
    filtered: list[dict[str, Any]] = []

    items = plan.items or []
    if not items:
        return list(products)

    for product in products:
        if not any(_passes_hard_filters(product, item, plan, thresholds) for item in items):
            continue
        band = _budget_band(product, plan, thresholds)
        if band is None:
            continue
        tagged = dict(product)
        tagged["budget_band"] = band
        filtered.append(tagged)

    return filtered


def score_product_for_plan(
    product: dict[str, Any],
    plan: CanonicalSearchPlan,
) -> float:
    """Score a product against the plan using ranking_priorities as weight multipliers."""
    if not plan.items:
        return 0.0

    total = 0.0
    for item in plan.items:
        base = _soft_score(product, item)

        priority_bonus = 0.0
        for rank, dim in enumerate(item.ranking_priorities):
            weight = max(1.0, len(item.ranking_priorities) - rank)
            val = _get_product_attr(product, dim)
            if val is not None:
                try:
                    priority_bonus += weight * float(val)
                except (TypeError, ValueError):
                    priority_bonus += weight * 0.5

        total += base + priority_bonus * 0.01

    budget = (plan.global_constraints.budget if plan.global_constraints else None)
    if budget and budget.target_maximum:
        price = _product_price(product)
        if 0 < price <= budget.target_maximum:
            total += 0.5
        elif budget.stretch_maximum and price <= budget.stretch_maximum:
            total += 0.2

    return round(total, 4)
