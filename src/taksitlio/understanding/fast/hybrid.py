"""Merge remote FAST + deterministic constraint bags (ADR-009 hybrid final).

The remote model owns NeedProfile / intent / budget. Deterministic rules
supply Turkish negation/correction spans the model often omits. The merged
bag is re-validated before scoring or matcher use — never a silent lexical
catalog fallback.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from taksitlio.semantic_constraints import SemanticConstraintValidator
from taksitlio.semantic_constraints.domain import ValidatedSemanticConstraints


def _as_list(bag: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(bag, Mapping):
        return []
    raw = bag.get(key) or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            out.append(dict(item))
    return out


def merge_constraint_bags(
    model_bag: Mapping[str, Any] | None,
    deterministic_bag: Mapping[str, Any] | None,
    *,
    validator: Optional[SemanticConstraintValidator] = None,
) -> ValidatedSemanticConstraints:
    """Union model + deterministic bags; deterministic fills correction gaps."""

    validator = validator or SemanticConstraintValidator()
    merged: dict[str, Any] = {
        "positive": _as_list(model_bag, "positive") + _as_list(deterministic_bag, "positive"),
        "negative": _as_list(model_bag, "negative") + _as_list(deterministic_bag, "negative"),
        "corrections": _as_list(model_bag, "corrections")
        + _as_list(deterministic_bag, "corrections"),
    }
    return validator.validate(merged)


def hybrid_final_constraints(
    *,
    model_constraints: Mapping[str, Any] | None,
    deterministic_constraints: Mapping[str, Any] | None,
    validator: Optional[SemanticConstraintValidator] = None,
) -> dict[str, Any]:
    """Return matcher-shaped hybrid final constraint dict."""

    validated = merge_constraint_bags(
        model_constraints,
        deterministic_constraints,
        validator=validator,
    )
    return validated.to_matcher_dict()
