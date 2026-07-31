"""Wire ADR-010 product-query caches from infra (Redis or in-memory)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from taksitlio.config.settings import InfraSettings
from taksitlio.entity_resolution.cache import (
    AliasResolutionCache,
    InMemoryAliasResolutionCache,
    NoOpAliasResolutionCache,
)
from taksitlio.entity_resolution.redis_cache import RedisAliasResolutionCache
from taksitlio.product_query.query_cache import (
    InMemoryPopularQueryCache,
    NoOpPopularQueryCache,
    PopularQueryCache,
    RedisBestOfferCache,
    RedisPopularQueryCache,
)


@dataclass(frozen=True)
class ProductQueryCaches:
    alias: AliasResolutionCache
    popular: PopularQueryCache
    best_offer: PopularQueryCache  # same protocol: get/put JSON blobs
    alias_ttl_seconds: int
    popular_ttl_seconds: int
    best_offer_ttl_seconds: int
    catalog_cache_version: str = "catalog-v0"


def build_product_query_caches(
    settings: InfraSettings,
    *,
    redis: Any = None,
) -> ProductQueryCaches:
    prefix = settings.redis_key_prefix.rstrip(":")
    if redis is not None:
        return ProductQueryCaches(
            alias=RedisAliasResolutionCache(
                redis, key_prefix=f"{prefix}:alias"
            ),
            popular=RedisPopularQueryCache(
                redis, key_prefix=f"{prefix}:popular"
            ),
            best_offer=RedisBestOfferCache(
                redis, key_prefix=f"{prefix}:best_offer"
            ),
            alias_ttl_seconds=settings.alias_cache_ttl_seconds,
            popular_ttl_seconds=settings.popular_query_cache_ttl_seconds,
            best_offer_ttl_seconds=settings.best_offer_cache_ttl_seconds,
            catalog_cache_version=settings.catalog_cache_version,
        )

    if settings.allow_in_memory:
        return ProductQueryCaches(
            alias=InMemoryAliasResolutionCache(),
            popular=InMemoryPopularQueryCache(),
            best_offer=InMemoryPopularQueryCache(),
            alias_ttl_seconds=settings.alias_cache_ttl_seconds,
            popular_ttl_seconds=settings.popular_query_cache_ttl_seconds,
            best_offer_ttl_seconds=settings.best_offer_cache_ttl_seconds,
            catalog_cache_version=settings.catalog_cache_version,
        )

    return ProductQueryCaches(
        alias=NoOpAliasResolutionCache(),
        popular=NoOpPopularQueryCache(),
        best_offer=NoOpPopularQueryCache(),
        alias_ttl_seconds=settings.alias_cache_ttl_seconds,
        popular_ttl_seconds=settings.popular_query_cache_ttl_seconds,
        best_offer_ttl_seconds=settings.best_offer_cache_ttl_seconds,
        catalog_cache_version=settings.catalog_cache_version,
    )


def caches_from_container(container: Any) -> Optional[ProductQueryCaches]:
    extras = getattr(container, "extras", None) or {}
    caches = extras.get("product_query_caches")
    return caches if isinstance(caches, ProductQueryCaches) else None


__all__ = [
    "ProductQueryCaches",
    "build_product_query_caches",
    "caches_from_container",
]
