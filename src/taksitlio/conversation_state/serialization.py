"""Deterministic serialization for conversation state payloads."""

from __future__ import annotations

import json
import math
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from taksitlio.conversation_state.domain import ConversationState
from taksitlio.conversation_state.errors import (
    ConversationSchemaUnsupported,
    ConversationStateValidationError,
)
from taksitlio.conversation_state.domain import CURRENT_SCHEMA_VERSION, SCHEMA_VERSION_V1


class ConversationStateMigrator:
    def supports(self, source_version: str, target_version: str) -> bool:
        return source_version == target_version == CURRENT_SCHEMA_VERSION

    def migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        version = str(payload.get("schema_version") or "")
        if version not in {SCHEMA_VERSION_V1, CURRENT_SCHEMA_VERSION}:
            raise ConversationSchemaUnsupported(
                f"Unsupported conversation schema_version: {version}"
            )
        return payload


def dumps_canonical(payload: dict[str, Any]) -> str:
    """UTF-8 JSON with stable key ordering; rejects NaN/Infinity."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def loads_canonical(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw, parse_constant=_reject_constant)
    if not isinstance(data, dict):
        raise ConversationStateValidationError("Payload must be a JSON object")
    return data


def serialize_state(state: ConversationState) -> str:
    return dumps_canonical(state.to_payload_dict())


def deserialize_state(
    raw: str | bytes,
    *,
    migrator: ConversationStateMigrator | None = None,
) -> ConversationState:
    migrator = migrator or ConversationStateMigrator()
    payload = loads_canonical(raw)
    payload = migrator.migrate(payload)
    return ConversationState.from_payload_dict(payload)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise ConversationStateValidationError("datetime must be timezone-aware")
        return obj.isoformat().replace("+00:00", "Z")
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ConversationStateValidationError("NaN/Infinity not allowed")
        return obj
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _reject_constant(name: str) -> None:
    raise ConversationStateValidationError(f"Disallowed JSON constant: {name}")
