"""ADR-010 — product query cache wiring + Redis JSON helpers."""

from __future__ import annotations

import pytest

from taksitlio.config.settings import InfraSettings
from taksitlio.entity_resolution.redis_cache import RedisAliasResolutionCache
from taksitlio.product_query.cache_wiring import build_product_query_caches
from taksitlio.product_query.query_cache import (
    InMemoryPopularQueryCache,
    RedisPopularQueryCache,
    popular_query_cache_key,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = None) -> None:  # type: ignore[assignment]
        self.store[key] = value.encode("utf-8") if isinstance(value, str) else value

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def scan_iter(self, match: str = "*", count: int = 100):
        # naive glob: prefix*
        prefix = match[:-1] if match.endswith("*") else match
        for k in list(self.store):
            if k.startswith(prefix):
                yield k


@pytest.mark.asyncio
async def test_build_caches_with_redis() -> None:
    settings = InfraSettings.from_env(allow_missing=True)
    redis = _FakeRedis()
    caches = build_product_query_caches(settings, redis=redis)
    assert isinstance(caches.alias, RedisAliasResolutionCache)
    assert isinstance(caches.popular, RedisPopularQueryCache)
    await caches.alias.put("abc", {"action": "AUTO_SELECT"}, ttl_seconds=60)
    assert await caches.alias.get("abc") == {"action": "AUTO_SELECT"}


@pytest.mark.asyncio
async def test_popular_query_roundtrip_memory() -> None:
    cache = InMemoryPopularQueryCache()
    key = popular_query_cache_key(
        utterance="laptop istiyorum",
        ranking_mode="BEST_OVERALL_VALUE",
        cache_version="v1",
    )
    await cache.put(key, {"cards": [{"product_id": "p1"}]}, ttl_seconds=30)
    hit = await cache.get(key)
    assert hit is not None
    assert hit["cards"][0]["product_id"] == "p1"


@pytest.mark.asyncio
async def test_in_memory_container_exposes_caches() -> None:
    from taksitlio.app.container import build_in_memory_container

    container = build_in_memory_container()
    caches = container.extras.get("product_query_caches")
    assert caches is not None
    await caches.popular.put("k", {"ok": True}, ttl_seconds=10)
    assert await caches.popular.get("k") == {"ok": True}
    await container.aclose()
