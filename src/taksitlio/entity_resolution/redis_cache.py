"""Optional Redis-backed alias resolution cache (ADR-010 §51)."""

from __future__ import annotations

import json
from typing import Any, Optional


class RedisAliasResolutionCache:
    """Thin Redis JSON cache. Catalog bumps should change key version, not rely on flush."""

    def __init__(self, redis_client: Any, *, key_prefix: str = "taksitlio:alias") -> None:
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
        # Prefer versioned keys over SCAN deletes in production.
        pattern = self._key(f"{prefix}*")
        removed = 0
        async for k in self._redis.scan_iter(match=pattern, count=100):
            await self._redis.delete(k)
            removed += 1
        return removed


__all__ = ["RedisAliasResolutionCache"]
