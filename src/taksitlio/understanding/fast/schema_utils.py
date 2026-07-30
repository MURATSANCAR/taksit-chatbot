"""Load + validate the NeedProfile JSON schema for FAST outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from taksitlio.understanding.fast.errors import NeedProfileSchemaError


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "need_profile.schema.json"
)


def _load_schema() -> dict:
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


_NEED_PROFILE_SCHEMA = _load_schema()
_VALIDATOR = jsonschema.Draft202012Validator(_NEED_PROFILE_SCHEMA)


def validate_need_profile(payload: Any) -> None:
    """Raise :class:`NeedProfileSchemaError` when ``payload`` is invalid."""

    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda e: list(e.path))
    if not errors:
        return
    issues = []
    for err in errors:
        loc = "/".join(str(p) for p in err.path) or "<root>"
        issues.append(f"{loc}: {err.message}")
    raise NeedProfileSchemaError(
        f"need_profile invalid: {len(issues)} issue(s)", issues=issues
    )


def build_empty_need_profile(
    *,
    utterance: str,
    intent: str = "PRODUCT_PURCHASE",
    intent_confidence: float = 0.7,
    confidence: float = 0.7,
) -> dict:
    """Return a minimal but schema-valid NeedProfile stub."""

    return {
        "intent": {"type": intent, "confidence": intent_confidence},
        "need_description": (utterance or "").strip()[:500] or "…",
        "budget": {
            "type": "UNKNOWN",
            "value": None,
            "minimum": None,
            "maximum": None,
            "monthly_payment": None,
            "currency": "TRY",
        },
        "preferences": [],
        "usage_context": [],
        "entities": [],
        "ambiguities": [],
        "clarification": {"required": False, "question_intent": None},
        "confidence": confidence,
    }


__all__ = ["build_empty_need_profile", "validate_need_profile"]
