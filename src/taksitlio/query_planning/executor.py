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


def _get_product_attr(product: dict[str, Any], dimension: str) -> Any:
    """Look up a dimension value in product metadata/attrs/top-level."""
    if dimension in product:
        return product[dimension]
    meta = product.get("metadata") or product.get("attrs") or {}
    if isinstance(meta, Mapping) and dimension in meta:
        return meta[dimension]
    specs = product.get("specifications") or {}
    if isinstance(specs, Mapping) and dimension in specs:
        return specs[dimension]
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
        over_target = price > budget.target_maximum
        if over_target:
            if budget.stretch_maximum is not None and price <= budget.stretch_maximum:
                threshold = float(exception_thresholds.get("price_advantage_ratio", 0.10))
                advantage = (budget.target_maximum - price) / budget.target_maximum
                if abs(advantage) < threshold:
                    pass
                else:
                    pass
            else:
                return False

    for exc in item.excluded_constraints:
        result = _evaluate_constraint(product, exc)
        if result is True:
            return False

    for c in item.hard_constraints:
        result = _evaluate_constraint(product, c)
        # Missing category/merchant fields defer to progressive_results matching.
        if result is None and c.dimension in {"category", "merchant"}:
            continue
        if result is False:
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


def filter_products_by_plan(
    products: list[dict[str, Any]],
    plan: CanonicalSearchPlan,
    *,
    exception_thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Filter a product list using the plan's hard constraints and exclusions.

    Products that pass hard filters but violate soft preferences are kept
    (they will rank lower via scoring).
    """
    thresholds = exception_thresholds or {}
    filtered: list[dict[str, Any]] = []

    items = plan.items or []
    if not items:
        return list(products)

    for product in products:
        if any(_passes_hard_filters(product, item, plan, thresholds) for item in items):
            filtered.append(product)

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
