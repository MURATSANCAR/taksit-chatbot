"""Detect and report constraint conflicts within a canonical search plan."""

from __future__ import annotations

from typing import Any

from taksitlio.query_planning.models import (
    CanonicalSearchPlan,
    ConstraintOperator,
    ConstraintStrength,
    PlanConstraint,
    PlanItem,
)


def _operators_conflict(a: ConstraintOperator, b: ConstraintOperator) -> bool:
    """True when the two operators on the same dimension are semantically opposed."""
    opposing = {
        (ConstraintOperator.EQ, ConstraintOperator.NEQ),
        (ConstraintOperator.GT, ConstraintOperator.LT),
        (ConstraintOperator.GT, ConstraintOperator.LTE),
        (ConstraintOperator.GTE, ConstraintOperator.LT),
        (ConstraintOperator.IN, ConstraintOperator.NOT_IN),
        (ConstraintOperator.CONTAINS, ConstraintOperator.EXCLUDES),
    }
    pair = (a, b) if (a, b) in opposing else (b, a)
    return pair in opposing


def _values_overlap(a: Any, b: Any) -> bool:
    """Check if two constraint values target the same logical entity."""
    if a is None or b is None:
        return False
    if isinstance(a, (list, tuple, set)) and isinstance(b, (list, tuple, set)):
        return bool(set(a) & set(b))
    return a == b


def _detect_item_conflicts(item: PlanItem) -> list[dict[str, Any]]:
    """Find HARD-vs-excluded and HARD-vs-HARD opposing conflicts in one item."""
    conflicts: list[dict[str, Any]] = []
    hard_by_dim: dict[str, list[PlanConstraint]] = {}
    for c in item.hard_constraints:
        hard_by_dim.setdefault(c.dimension, []).append(c)

    for exc in item.excluded_constraints:
        for hc in hard_by_dim.get(exc.dimension, []):
            if _values_overlap(hc.value, exc.value):
                conflicts.append({
                    "type": "HARD_EXCLUDED_SAME_VALUE",
                    "item_id": item.item_id,
                    "dimension": hc.dimension,
                    "hard_constraint_id": hc.constraint_id,
                    "excluded_constraint_id": exc.constraint_id,
                    "hard_value": hc.value,
                    "excluded_value": exc.value,
                })
            elif hc.dimension == exc.dimension:
                conflicts.append({
                    "type": "HARD_EXCLUDED_SAME_DIMENSION",
                    "item_id": item.item_id,
                    "dimension": hc.dimension,
                    "hard_constraint_id": hc.constraint_id,
                    "excluded_constraint_id": exc.constraint_id,
                })

    dims_seen: dict[str, PlanConstraint] = {}
    for c in item.hard_constraints:
        if c.dimension in dims_seen:
            prev = dims_seen[c.dimension]
            if _operators_conflict(prev.operator, c.operator):
                conflicts.append({
                    "type": "HARD_OPPOSING_OPERATORS",
                    "item_id": item.item_id,
                    "dimension": c.dimension,
                    "constraint_a": prev.constraint_id,
                    "constraint_b": c.constraint_id,
                })
        else:
            dims_seen[c.dimension] = c

    return conflicts


def _detect_global_conflicts(plan: CanonicalSearchPlan) -> list[dict[str, Any]]:
    """Detect conflicts in global constraints (e.g. merchant in both allowed and excluded)."""
    conflicts: list[dict[str, Any]] = []
    gc = plan.global_constraints
    if not gc:
        return conflicts

    if gc.allowed_merchants and gc.excluded_merchants:
        overlap = set(gc.allowed_merchants) & set(gc.excluded_merchants)
        if overlap:
            conflicts.append({
                "type": "MERCHANT_ALLOWED_AND_EXCLUDED",
                "values": sorted(overlap),
            })

    budget = gc.budget
    if budget:
        if budget.minimum is not None and budget.target_maximum is not None:
            if budget.minimum > budget.target_maximum:
                conflicts.append({
                    "type": "BUDGET_MIN_EXCEEDS_MAX",
                    "minimum": budget.minimum,
                    "target_maximum": budget.target_maximum,
                })
        if budget.stretch_maximum is not None and budget.target_maximum is not None:
            if budget.stretch_maximum < budget.target_maximum:
                conflicts.append({
                    "type": "STRETCH_BELOW_TARGET",
                    "target_maximum": budget.target_maximum,
                    "stretch_maximum": budget.stretch_maximum,
                })

    return conflicts


def resolve_conflicts(plan: CanonicalSearchPlan) -> CanonicalSearchPlan:
    """Populate ``plan.conflicts`` with detected issues. Does NOT auto-relax HARD constraints."""
    all_conflicts: list[dict[str, Any]] = []

    for item in plan.items:
        all_conflicts.extend(_detect_item_conflicts(item))

    all_conflicts.extend(_detect_global_conflicts(plan))

    plan.conflicts = all_conflicts
    if all_conflicts:
        plan.clarification_required = True

    return plan
