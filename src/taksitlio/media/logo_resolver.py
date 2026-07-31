"""Logo CDN resolvers from media catalog (ADR-011 P2).

Never invents logos. Only READY media_assets.cdn_url values from verified links.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol


@dataclass(frozen=True)
class LogoResolver:
    """entity_id (string) → CDN URL. Missing means IMAGE_UNAVAILABLE / text fallback."""

    merchant_logos: Mapping[str, str] = field(default_factory=dict)
    brand_logos: Mapping[str, str] = field(default_factory=dict)
    institution_logos: Mapping[str, str] = field(default_factory=dict)

    def merchant(self, entity_id: str | int | None) -> Optional[str]:
        if entity_id is None:
            return None
        return self.merchant_logos.get(str(entity_id))

    def brand(self, entity_id: str | int | None) -> Optional[str]:
        if entity_id is None:
            return None
        return self.brand_logos.get(str(entity_id))

    def institution(self, entity_id: str | int | None) -> Optional[str]:
        if entity_id is None:
            return None
        return self.institution_logos.get(str(entity_id))


class LogoCatalog(Protocol):
    async def load(self) -> LogoResolver: ...


@dataclass
class InMemoryLogoCatalog:
    resolver: LogoResolver = field(default_factory=LogoResolver)

    async def load(self) -> LogoResolver:
        return self.resolver

    def put_merchant(self, entity_id: str, cdn_url: str) -> None:
        logos = dict(self.resolver.merchant_logos)
        logos[str(entity_id)] = cdn_url
        self.resolver = LogoResolver(
            merchant_logos=logos,
            brand_logos=dict(self.resolver.brand_logos),
            institution_logos=dict(self.resolver.institution_logos),
        )

    def put_brand(self, entity_id: str, cdn_url: str) -> None:
        logos = dict(self.resolver.brand_logos)
        logos[str(entity_id)] = cdn_url
        self.resolver = LogoResolver(
            merchant_logos=dict(self.resolver.merchant_logos),
            brand_logos=logos,
            institution_logos=dict(self.resolver.institution_logos),
        )

    def put_institution(self, entity_id: str, cdn_url: str) -> None:
        logos = dict(self.resolver.institution_logos)
        logos[str(entity_id)] = cdn_url
        self.resolver = LogoResolver(
            merchant_logos=dict(self.resolver.merchant_logos),
            brand_logos=dict(self.resolver.brand_logos),
            institution_logos=logos,
        )


_LOGO_SQL = """
SELECT entity_id::text AS entity_id, cdn_url FROM (
    SELECT {id_col} AS entity_id, ma.cdn_url,
           ROW_NUMBER() OVER (
               PARTITION BY {id_col}
               ORDER BY CASE mm.role WHEN 'LOGO' THEN 0 WHEN 'PRIMARY' THEN 1 ELSE 2 END,
                        mm.is_primary DESC, mm.id DESC
           ) AS rn
    FROM {link_table} mm
    JOIN media_assets ma ON ma.id = mm.media_asset_id
    WHERE ma.status = 'READY'
      AND ma.cdn_url IS NOT NULL
      AND mm.role IN ('LOGO', 'PRIMARY', 'ICON')
      AND (mm.valid_until IS NULL OR mm.valid_until > NOW())
) t WHERE rn = 1
"""


class PostgresLogoCatalog:
    """Loads merchant / brand / institution logos from media link tables."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def load(self) -> LogoResolver:
        async with self._pool.acquire() as conn:
            merchants = await conn.fetch(
                _LOGO_SQL.format(id_col="merchant_id", link_table="merchant_media")
            )
            brands = await conn.fetch(
                _LOGO_SQL.format(id_col="brand_id", link_table="brand_media")
            )
            institutions = await conn.fetch(
                _LOGO_SQL.format(
                    id_col="institution_id", link_table="financial_institution_media"
                )
            )
        return LogoResolver(
            merchant_logos={str(r["entity_id"]): str(r["cdn_url"]) for r in merchants},
            brand_logos={str(r["entity_id"]): str(r["cdn_url"]) for r in brands},
            institution_logos={
                str(r["entity_id"]): str(r["cdn_url"]) for r in institutions
            },
        )


__all__ = [
    "InMemoryLogoCatalog",
    "LogoCatalog",
    "LogoResolver",
    "PostgresLogoCatalog",
]
