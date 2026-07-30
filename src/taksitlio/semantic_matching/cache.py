"""Cache abstractions for the semantic matcher.

The cache key is always
    SHA-256(normalize(input) + catalog_revision + embedding_profile_id
            + policy_version + locale)
The raw user text is never used as a key.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from taksitlio.semantic_matching.domain import CategoryMatchResult, MatchQuery, SemanticMatchPolicy


def _normalize(text: str) -> str:
    lowered = (text or "").casefold().strip()
    return " ".join(lowered.split())


def build_cache_key(query: MatchQuery, policy: SemanticMatchPolicy) -> str:
    normalized = _normalize(query.text)
    payload = "|".join(
        [
            normalized,
            str(query.catalog_id),
            str(query.catalog_revision),
            str(query.embedding_profile_id),
            str(policy.policy_version),
            str(query.locale),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CategoryMatchCache(Protocol):
    async def get(self, key: str) -> Optional[CategoryMatchResult]: ...

    async def put(
        self, key: str, result: CategoryMatchResult, *, ttl_seconds: int
    ) -> None: ...


class NoOpCategoryMatchCache:
    async def get(self, key: str) -> Optional[CategoryMatchResult]:
        return None

    async def put(
        self, key: str, result: CategoryMatchResult, *, ttl_seconds: int
    ) -> None:
        return None


@dataclass
class _Entry:
    result: CategoryMatchResult
    expires_at: float


class InMemoryCategoryMatchCache:
    def __init__(self) -> None:
        self._store: dict[str, _Entry] = {}

    async def get(self, key: str) -> Optional[CategoryMatchResult]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return entry.result

    async def put(
        self, key: str, result: CategoryMatchResult, *, ttl_seconds: int
    ) -> None:
        ttl = max(0, int(ttl_seconds))
        self._store[key] = _Entry(
            result=result,
            expires_at=time.monotonic() + ttl if ttl else float("inf"),
        )


__all__ = [
    "CategoryMatchCache",
    "InMemoryCategoryMatchCache",
    "NoOpCategoryMatchCache",
    "build_cache_key",
]
