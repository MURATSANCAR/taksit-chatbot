"""Validate raw plan dicts against the canonical schema."""

from __future__ import annotations

import json
from typing import Any

from taksitlio.query_planning.models import CanonicalSearchPlan, ConstraintStrength
from taksitlio.query_planning.schema import CANONICAL_PLAN_SCHEMA, FORBIDDEN_PLAN_FIELDS


class PlanValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Plan validation failed: {'; '.join(errors)}")


def _collect_all_string_values(obj: Any) -> list[str]:
    """Recursively collect every string value in a nested structure."""
    results: list[str] = []
    if isinstance(obj, str):
        results.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            results.extend(_collect_all_string_values(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            results.extend(_collect_all_string_values(v))
    return results


def _check_forbidden_fields(data: dict[str, Any]) -> list[str]:
    """Reject plans that contain forbidden identifier values at any depth."""
    errors: list[str] = []
    all_values = _collect_all_string_values(data)
    for val in all_values:
        lower = val.strip().lower()
        if lower in FORBIDDEN_PLAN_FIELDS:
            errors.append(f"Forbidden field value detected: '{val}'")
    _check_forbidden_keys(data, errors)
    return errors


def _check_forbidden_keys(obj: Any, errors: list[str]) -> None:
    if isinstance(obj, dict):
        for key in obj:
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_PLAN_FIELDS:
                errors.append(f"Forbidden key detected: '{key}'")
            _check_forbidden_keys(obj[key], errors)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _check_forbidden_keys(item, errors)


def _check_unknown_fields(data: dict[str, Any], schema_props: dict[str, Any], path: str) -> list[str]:
    """Flag keys not present in the schema properties."""
    errors: list[str] = []
    known = set(schema_props.keys())
    for key in data:
        if key not in known:
            errors.append(f"Unknown field '{key}' at {path}")
    return errors


def _check_conflicts_structural(data: dict[str, Any]) -> list[str]:
    """Detect HARD vs excluded on the same dimension within each item."""
    errors: list[str] = []
    for item in data.get("items") or []:
        hard_dims: set[str] = set()
        for c in item.get("hard_constraints") or []:
            dim = c.get("dimension", "")
            if dim:
                hard_dims.add(dim)
        for c in item.get("excluded_constraints") or []:
            dim = c.get("dimension", "")
            if dim and dim in hard_dims:
                errors.append(
                    f"Item '{item.get('item_id', '?')}': HARD constraint and exclusion "
                    f"on same dimension '{dim}'"
                )
    return errors


def validate_plan(
    data: dict[str, Any],
    *,
    strict: bool = False,
) -> CanonicalSearchPlan:
    """Validate a plan dict and return a typed ``CanonicalSearchPlan``.

    In strict mode, unknown top-level and item-level fields are rejected.
    Forbidden identifier keys/values are always rejected.
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        raise PlanValidationError(["Plan must be a dict"])

    if data.get("plan_version") != "v1":
        errors.append(f"Unsupported plan_version: {data.get('plan_version')!r}")

    rt = data.get("request_type")
    valid_types = {e.value for e in __import__("taksitlio.query_planning.models", fromlist=["RequestType"]).RequestType}
    if rt and rt not in valid_types:
        errors.append(f"Invalid request_type: {rt!r}")

    if not data.get("items"):
        errors.append("Plan must contain at least one item")

    errors.extend(_check_forbidden_fields(data))

    if strict:
        top_props = CANONICAL_PLAN_SCHEMA.get("properties", {})
        errors.extend(_check_unknown_fields(data, top_props, "root"))
        item_props = top_props.get("items", {}).get("items", {}).get("properties", {})
        if item_props:
            for idx, item in enumerate(data.get("items") or []):
                if isinstance(item, dict):
                    errors.extend(_check_unknown_fields(item, item_props, f"items[{idx}]"))

    errors.extend(_check_conflicts_structural(data))

    if errors:
        raise PlanValidationError(errors)

    return CanonicalSearchPlan.from_dict(data)
