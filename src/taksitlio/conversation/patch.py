"""Safe conversation patch application — allowlisted paths, no model old_value."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

ALLOWED_PATHS = frozenset(
    {
        "/need_description",
        "/budget",
        "/budget/type",
        "/budget/value",
        "/budget/minimum",
        "/budget/maximum",
        "/budget/monthly_payment",
        "/budget/currency",
        "/preferences",
        "/usage_context",
        "/entities",
        "/ambiguities",
        "/clarification",
        "/clarification/required",
        "/clarification/question_intent",
        "/intent",
        "/intent/type",
        "/intent/confidence",
        "/confidence",
    }
)

ALLOWED_OPERATIONS = frozenset(
    {"SET", "REMOVE", "APPEND", "REPLACE_COLLECTION", "RESET_NEED"}
)


class ConversationPatchError(ValueError):
    """Raised when a patch violates allowlist or schema rules."""


def apply_conversation_patch(
    current: dict[str, Any] | None,
    patch: dict[str, Any],
    *,
    allowed_paths: Iterable[str] | None = None,
    expected_version: int | None = None,
    current_version: int | None = None,
) -> dict[str, Any]:
    """
    Apply a typed patch onto structured session need state.

    - Model-supplied old_value is ignored even if present.
    - Paths must be allowlisted JSON Pointers.
    - Optional optimistic version check (Redis locking comes later).
    """
    if expected_version is not None and current_version is not None:
        if expected_version != current_version:
            raise ConversationPatchError(
                f"Version conflict: expected {expected_version}, got {current_version}"
            )

    operation = patch.get("operation")
    if operation not in ALLOWED_OPERATIONS:
        raise ConversationPatchError(f"Unsupported operation: {operation}")

    # Reject legacy / unsafe contracts explicitly
    if "old_value" in patch or "updates" in patch or "field" in patch:
        raise ConversationPatchError(
            "Legacy update fields (old_value/updates/field) are not accepted"
        )

    if operation == "RESET_NEED":
        profile = patch.get("need_profile")
        if not isinstance(profile, dict):
            raise ConversationPatchError("RESET_NEED requires need_profile object")
        return deepcopy(profile)

    path = patch.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ConversationPatchError("path must be a JSON Pointer starting with /")

    allow = frozenset(allowed_paths) if allowed_paths is not None else ALLOWED_PATHS
    if path not in allow:
        raise ConversationPatchError(f"Path not allowlisted: {path}")

    base = deepcopy(current or {})

    if operation == "SET":
        if "value" not in patch:
            raise ConversationPatchError("SET requires value")
        _set_pointer(base, path, patch.get("value"))
        return base

    if operation == "REMOVE":
        _remove_pointer(base, path)
        return base

    if operation == "APPEND":
        existing = _get_pointer(base, path)
        if existing is None:
            existing = []
            _set_pointer(base, path, existing)
        if not isinstance(existing, list):
            raise ConversationPatchError(f"APPEND target is not a list: {path}")
        existing.append(patch.get("value"))
        return base

    if operation == "REPLACE_COLLECTION":
        value = patch.get("value")
        if not isinstance(value, list):
            raise ConversationPatchError("REPLACE_COLLECTION requires list value")
        _set_pointer(base, path, deepcopy(value))
        return base

    raise ConversationPatchError(f"Unsupported operation: {operation}")


# Back-compat alias used by older imports during transition
def apply_conversation_update(
    current: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    """
    Compatibility wrapper.

    New typed patches go through apply_conversation_patch.
    Legacy dotted UPDATE payloads are rejected (security).
    """
    if update.get("operation") in ALLOWED_OPERATIONS:
        return apply_conversation_patch(current, update)
    raise ConversationPatchError(
        "Legacy conversation update format is disabled; use typed SET/REMOVE/APPEND patches"
    )


def _pointer_parts(path: str) -> list[str]:
    if path == "/":
        return []
    return [p.replace("~1", "/").replace("~0", "~") for p in path.lstrip("/").split("/")]


def _set_pointer(target: dict[str, Any], path: str, value: Any) -> None:
    parts = _pointer_parts(path)
    if not parts:
        raise ConversationPatchError("Cannot SET document root")
    node: Any = target
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _get_pointer(target: dict[str, Any], path: str) -> Any:
    node: Any = target
    for part in _pointer_parts(path):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _remove_pointer(target: dict[str, Any], path: str) -> None:
    parts = _pointer_parts(path)
    if not parts:
        raise ConversationPatchError("Cannot REMOVE document root")
    node: Any = target
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    if isinstance(node, dict):
        node.pop(parts[-1], None)
