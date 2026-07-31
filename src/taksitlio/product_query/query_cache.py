"""Popular-query / best-offer Redis caches (ADR-010 §51).

Never source of truth — catalog/DB wins; bump cache_version on change.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional, Protocol


def popular_query_cache_key(
    *,
    utterance: str,
    ranking_mode: str,
    cache_version: str,
    locale: str = "tr-TR",
    merchant_id: Optional[str] = None,
) -> str:
    normalized = " ".join((utterance or "").casefold().split())
    payload = "|".join(
        [
            "popular",
            normalized,
            ranking_mode,
            cache_version,
            locale,
            merchant_id or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def best_offer_cache_key(
    *,
    product_id: str,
    cache_version: str,
    institution_id: Optional[str] = None,
) -> str:
    payload = "|".join(
        ["best_offer", product_id, cache_version, institution_id or ""]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PopularQueryCache(Protocol):
    async def get(self, key: str) -> Optional[dict[str, Any]]: ...

    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None: ...


class NoOpPopularQueryCache:
    async def get(self, key: str) -> Optional[dict[str, Any]]:
        return None

    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        return None


class InMemoryPopularQueryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[dict[str, Any], float]] = {}
        self._version_tag = "v0"

    def set_version(self, version: str) -> None:
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


class RedisJsonCache:
    """Shared Redis JSON blob helper for alias / popular / best-offer keys."""

    def __init__(self, redis_client: Any, *, key_prefix: str) -> None:
        self._redis = redis_client
        self._prefix = key_prefix.rstrip(":")

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        await self._redis.set(self._key(key), payload, ex=max(1, int(ttl_seconds)))

    async def invalidate_prefix(self, prefix: str) -> int:
        pattern = self._key(f"{prefix}*")
        removed = 0
        async for k in self._redis.scan_iter(match=pattern, count=100):
            await self._redis.delete(k)
            removed += 1
        return removed


class RedisPopularQueryCache(RedisJsonCache):
    def __init__(self, redis_client: Any, *, key_prefix: str = "taksitlio:popular") -> None:
        super().__init__(redis_client, key_prefix=key_prefix)


class RedisBestOfferCache(RedisJsonCache):
    def __init__(self, redis_client: Any, *, key_prefix: str = "taksitlio:best_offer") -> None:
        super().__init__(redis_client, key_prefix=key_prefix)


__all__ = [
    "InMemoryPopularQueryCache",
    "NoOpPopularQueryCache",
    "PopularQueryCache",
    "RedisBestOfferCache",
    "RedisJsonCache",
    "RedisPopularQueryCache",
    "best_offer_cache_key",
    "popular_query_cache_key",
]
