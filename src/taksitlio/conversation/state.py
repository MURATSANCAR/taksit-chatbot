"""Apply conversation UPDATE operations onto Redis session need state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def apply_conversation_update(
    current: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge a ConversationUpdate payload into the structured session need profile.

    Full chat history is never required; only the structured state is mutated.
    """
    operation = update.get("operation")
    if operation == "RESET":
        return deepcopy(update.get("need_profile") or {})

    if operation == "REPLACE":
        profile = update.get("need_profile")
        if not isinstance(profile, dict):
            raise ValueError("REPLACE requires need_profile")
        return deepcopy(profile)

    if operation == "CLARIFY":
        return deepcopy(current or {})

    if operation != "UPDATE":
        raise ValueError(f"Unsupported operation: {operation}")

    base = deepcopy(current or {})
    for item in update.get("updates") or []:
        field = item.get("field")
        if not isinstance(field, str) or not field:
            raise ValueError("UPDATE field must be a non-empty string")
        _set_dotted(base, field, item.get("new_value"))
    return base


def _set_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node: Any = target
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
