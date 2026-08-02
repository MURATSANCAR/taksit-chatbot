"""Immutable state-operation reducer for plan constraints."""

from __future__ import annotations

import copy
import datetime
from typing import Any, Optional

from taksitlio.query_planning.models import (
    CanonicalSearchPlan,
    ConstraintStrength,
    PlanConstraint,
    StateOperation,
    StateOperationRecord,
)


class StaleVersionError(ValueError):
    """Raised when *query_version* does not match the current state version."""


def _find_constraint_in_plan(
    plan: CanonicalSearchPlan,
    constraint_id: str,
) -> tuple[Optional[PlanConstraint], str, int, int]:
    """Locate a constraint by id. Returns (constraint, list_name, item_idx, constraint_idx)."""
    for i, item in enumerate(plan.items):
        for bucket_name, bucket in (
            ("hard_constraints", item.hard_constraints),
            ("soft_preferences", item.soft_preferences),
            ("excluded_constraints", item.excluded_constraints),
        ):
            for j, c in enumerate(bucket):
                if c.constraint_id == constraint_id:
                    return c, bucket_name, i, j
    return None, "", -1, -1


def _snapshot_state(state_dict: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(state_dict)


def _snapshot_plan(plan: CanonicalSearchPlan) -> dict[str, Any]:
    return copy.deepcopy(plan.to_dict())


def _make_record(
    operation: StateOperation,
    target_constraint_id: str,
    value: Any,
    query_version: int,
    state_dict: dict[str, Any],
    plan: CanonicalSearchPlan,
) -> StateOperationRecord:
    return StateOperationRecord(
        operation=operation,
        target_constraint_id=target_constraint_id,
        value=value,
        query_version=query_version,
        previous_state_snapshot=_snapshot_state(state_dict),
        previous_plan_snapshot=_snapshot_plan(plan),
        timestamp=datetime.datetime.utcnow().isoformat(),
    )


def _apply_add(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    value: Any,
    target_constraint_id: str,
) -> None:
    if not isinstance(value, dict):
        return
    constraint = PlanConstraint.from_dict(value)
    constraint.constraint_id = target_constraint_id or constraint.constraint_id
    if plan.items:
        item = plan.items[0]
        if constraint.strength == ConstraintStrength.HARD:
            item.hard_constraints.append(constraint)
        else:
            item.soft_preferences.append(constraint)


def _apply_remove(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    target_constraint_id: str,
) -> None:
    for item in plan.items:
        for bucket in (item.hard_constraints, item.soft_preferences, item.excluded_constraints):
            for i, c in enumerate(bucket):
                if c.constraint_id == target_constraint_id:
                    bucket.pop(i)
                    cancelled = state.setdefault("cancelled_constraints", [])
                    cancelled.append({
                        "constraint_id": target_constraint_id,
                        "reason": "removed",
                    })
                    return


def _apply_replace(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    target_constraint_id: str,
    value: Any,
) -> None:
    c, bucket_name, item_idx, c_idx = _find_constraint_in_plan(plan, target_constraint_id)
    if c is None or not isinstance(value, dict):
        return
    new_c = PlanConstraint.from_dict(value)
    new_c.constraint_id = target_constraint_id
    bucket = getattr(plan.items[item_idx], bucket_name)
    bucket[c_idx] = new_c


def _apply_relax(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    target_constraint_id: str,
) -> None:
    """Demote HARD -> SOFT."""
    for item in plan.items:
        for i, c in enumerate(item.hard_constraints):
            if c.constraint_id == target_constraint_id:
                c.strength = ConstraintStrength.SOFT
                item.hard_constraints.pop(i)
                item.soft_preferences.append(c)
                return


def _apply_require(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    target_constraint_id: str,
) -> None:
    """Promote SOFT -> HARD."""
    for item in plan.items:
        for i, c in enumerate(item.soft_preferences):
            if c.constraint_id == target_constraint_id:
                c.strength = ConstraintStrength.HARD
                item.soft_preferences.pop(i)
                item.hard_constraints.append(c)
                return


def _apply_prefer(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    target_constraint_id: str,
    value: Any,
) -> None:
    if not isinstance(value, dict):
        return
    constraint = PlanConstraint.from_dict(value)
    constraint.constraint_id = target_constraint_id or constraint.constraint_id
    constraint.strength = ConstraintStrength.SOFT
    if plan.items:
        plan.items[0].soft_preferences.append(constraint)


def _apply_temporary_exception(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    target_constraint_id: str,
    value: Any,
) -> None:
    from taksitlio.query_planning.models import ConditionalException

    c, _, item_idx, _ = _find_constraint_in_plan(plan, target_constraint_id)
    if c is None:
        return
    exc = ConditionalException(
        target_constraint_id=target_constraint_id,
        condition_code="temporary_user_override",
        stretch_value=value,
    )
    plan.items[item_idx].conditional_exceptions.append(exc)


def _apply_rollback(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
    history: list[StateOperationRecord],
) -> tuple[dict[str, Any], CanonicalSearchPlan]:
    """Pop the last record and restore the snapshot from before that operation."""
    if not history:
        return state, plan
    last = history.pop()
    restored_state = last.previous_state_snapshot or state
    restored_plan_dict = last.previous_plan_snapshot
    if restored_plan_dict:
        restored_plan = CanonicalSearchPlan.from_dict(restored_plan_dict)
    else:
        restored_plan = plan
    return restored_state, restored_plan


def _apply_clear(
    state: dict[str, Any],
    plan: CanonicalSearchPlan,
) -> None:
    for item in plan.items:
        for c in item.hard_constraints + item.soft_preferences:
            cancelled = state.setdefault("cancelled_constraints", [])
            cancelled.append({"constraint_id": c.constraint_id, "reason": "cleared"})
        item.hard_constraints.clear()
        item.soft_preferences.clear()
        item.conditional_exceptions.clear()


def apply_operation(
    state_dict: dict[str, Any],
    plan_dict: dict[str, Any],
    operation: StateOperation | str,
    *,
    target_constraint_id: str = "",
    value: Any = None,
    query_version: int,
    history: list[StateOperationRecord] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], StateOperationRecord]:
    """Apply a state operation and return ``(new_state, new_plan_dict, record)``.

    Raises ``StaleVersionError`` when *query_version* does not match the
    state's current version.
    """
    if isinstance(operation, str):
        operation = StateOperation(operation)

    history = history if history is not None else []

    current_version = int(state_dict.get("state_version", 0))
    if query_version != current_version:
        raise StaleVersionError(
            f"Expected state_version={current_version}, got query_version={query_version}"
        )

    state = copy.deepcopy(state_dict)
    plan = CanonicalSearchPlan.from_dict(copy.deepcopy(plan_dict))

    record = _make_record(operation, target_constraint_id, value, query_version, state, plan)

    if operation == StateOperation.ROLLBACK:
        state, plan = _apply_rollback(state, plan, history)
    elif operation == StateOperation.ADD:
        _apply_add(state, plan, value, target_constraint_id)
    elif operation == StateOperation.REMOVE:
        _check_negation_resurrection(state, target_constraint_id)
        _apply_remove(state, plan, target_constraint_id)
    elif operation == StateOperation.REPLACE:
        _apply_replace(state, plan, target_constraint_id, value)
    elif operation == StateOperation.RELAX:
        _apply_relax(state, plan, target_constraint_id)
    elif operation == StateOperation.REQUIRE:
        _apply_require(state, plan, target_constraint_id)
    elif operation == StateOperation.PREFER:
        _apply_prefer(state, plan, target_constraint_id, value)
    elif operation == StateOperation.TEMPORARY_EXCEPTION:
        _apply_temporary_exception(state, plan, target_constraint_id, value)
    elif operation == StateOperation.CLEAR:
        _apply_clear(state, plan)

    state["state_version"] = current_version + 1

    return state, plan.to_dict(), record


def _check_negation_resurrection(state: dict[str, Any], constraint_id: str) -> None:
    """Ensure a previously-removed excluded constraint stays cancelled."""
    cancelled = state.get("cancelled_constraints") or []
    for entry in cancelled:
        if isinstance(entry, dict) and entry.get("constraint_id") == constraint_id:
            if entry.get("reason") in ("removed", "negation"):
                raise ValueError(
                    f"Cannot resurrect negated/removed constraint '{constraint_id}'"
                )
