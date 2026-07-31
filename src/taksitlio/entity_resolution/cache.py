"""Versioned alias / resolution cache (ADR-010 §51).

Cache is never source of truth — bump ``cache_version`` on catalog change.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional, Protocol

from taksitlio.entity_resolution import ResolutionResult


def resolution_cache_key(
    *,
    entity_type: str,
    query_text: str,
    cache_version: str,
    locale: str = "tr-TR",
) -> str:
    normalized = " ".join((query_text or "").casefold().split())
    payload = "|".join([entity_type, normalized, cache_version, locale])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AliasResolutionCache(Protocol):
    async def get(self, key: str) -> Optional[dict[str, Any]]: ...

    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None: ...

    async def invalidate_prefix(self, prefix: str) -> int:
        """Best-effort invalidation; returns removed count when known."""
        ...


class NoOpAliasResolutionCache:
    async def get(self, key: str) -> Optional[dict[str, Any]]:
        return None

    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        return None

    async def invalidate_prefix(self, prefix: str) -> int:
        return 0


class InMemoryAliasResolutionCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[dict[str, Any], float]] = {}
        self._version_tag = "v0"

    def set_version(self, version: str) -> None:
        """Invalidate all entries when catalog version changes."""

        if version != self._version_tag:
            self._store.clear()
            self._version_tag = version

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires = item
        if expires < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        self._store[key] = (value, time.monotonic() + max(0, int(ttl_seconds)))

    async def invalidate_prefix(self, prefix: str) -> int:
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            self._store.pop(k, None)
        return len(keys)


def resolution_to_cache_dict(result: ResolutionResult) -> dict[str, Any]:
    return {
        "input_text": result.input_text,
        "action": result.action.value,
        "resolved_entity_id": result.resolved_entity_id,
        "resolved_display_name": result.resolved_display_name,
        "match_type": None if result.match_type is None else result.match_type.value,
        "similarity": result.similarity,
        "confidence": result.confidence,
        "candidate_gap": result.candidate_gap,
        "candidates": [
            {
                "entity_id": c.entity_id,
                "display_name": c.display_name,
                "match_type": c.match_type.value,
                "similarity": c.similarity,
                "confidence": c.confidence,
            }
            for c in result.candidates
        ],
    }


def cache_dict_fingerprint(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "AliasResolutionCache",
    "InMemoryAliasResolutionCache",
    "NoOpAliasResolutionCache",
    "cache_dict_fingerprint",
    "resolution_cache_key",
    "resolution_to_cache_dict",
]
