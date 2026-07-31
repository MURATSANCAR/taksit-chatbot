"""Stable content hashing for delta sync (ADR-010 §43)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def content_hash(payload: Any) -> str:
    """SHA-256 over canonical JSON (sorted keys, no whitespace drift)."""

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["content_hash"]
