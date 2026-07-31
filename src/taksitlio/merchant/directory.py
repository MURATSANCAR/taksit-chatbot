"""Merchant directory — display names from catalog DB only (ADR-010 P11).

Never invents commercial names in application code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence


@dataclass(frozen=True)
class MerchantDirectoryEntry:
    id: int
    merchant_code: str
    display_name: str
    status: str = "ACTIVE"
    logo_cdn_url: Optional[str] = None


class MerchantDirectory(Protocol):
    async def get(self, merchant_id: int) -> Optional[MerchantDirectoryEntry]: ...

    async def get_by_code(self, merchant_code: str) -> Optional[MerchantDirectoryEntry]: ...

    async def get_display_name(self, merchant_id: int) -> Optional[str]: ...

    async def get_logo_cdn_url(self, merchant_id: int) -> Optional[str]: ...

    async def upsert(self, entry: MerchantDirectoryEntry) -> MerchantDirectoryEntry: ...

    async def list_active(self, *, limit: int = 200) -> Sequence[MerchantDirectoryEntry]: ...


class InMemoryMerchantDirectory:
    def __init__(self, entries: Sequence[MerchantDirectoryEntry] = ()) -> None:
        self._by_id: dict[int, MerchantDirectoryEntry] = {e.id: e for e in entries}

    async def get(self, merchant_id: int) -> Optional[MerchantDirectoryEntry]:
        return self._by_id.get(merchant_id)

    async def get_by_code(self, merchant_code: str) -> Optional[MerchantDirectoryEntry]:
        code = merchant_code.strip()
        for entry in self._by_id.values():
            if entry.merchant_code == code:
                return entry
        return None

    async def get_display_name(self, merchant_id: int) -> Optional[str]:
        row = self._by_id.get(merchant_id)
        if row is None or row.status != "ACTIVE":
            return None
        return row.display_name

    async def get_logo_cdn_url(self, merchant_id: int) -> Optional[str]:
        row = self._by_id.get(merchant_id)
        if row is None or row.status != "ACTIVE":
            return None
        return row.logo_cdn_url

    async def upsert(self, entry: MerchantDirectoryEntry) -> MerchantDirectoryEntry:
        for existing in self._by_id.values():
            if existing.merchant_code == entry.merchant_code:
                updated = MerchantDirectoryEntry(
                    id=existing.id,
                    merchant_code=entry.merchant_code,
                    display_name=entry.display_name,
                    status=entry.status,
                    logo_cdn_url=entry.logo_cdn_url if entry.logo_cdn_url is not None else existing.logo_cdn_url,
                )
                self._by_id[existing.id] = updated
                return updated
        new_id = entry.id
        if new_id <= 0 or new_id in self._by_id:
            new_id = (max(self._by_id.keys()) if self._by_id else 0) + 1
        stored = MerchantDirectoryEntry(
            id=new_id,
            merchant_code=entry.merchant_code,
            display_name=entry.display_name,
            status=entry.status,
            logo_cdn_url=entry.logo_cdn_url,
        )
        self._by_id[new_id] = stored
        return stored

    async def list_active(self, *, limit: int = 200) -> Sequence[MerchantDirectoryEntry]:
        rows = [e for e in self._by_id.values() if e.status == "ACTIVE"]
        rows.sort(key=lambda e: e.id)
        return tuple(rows[:limit])


class PostgresMerchantDirectory:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(self, merchant_id: int) -> Optional[MerchantDirectoryEntry]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT m.id, m.merchant_code, m.display_name, m.status,
                       logo.cdn_url AS logo_cdn_url
                FROM merchants m
                LEFT JOIN LATERAL (
                    SELECT ma.cdn_url
                    FROM merchant_media mm
                    JOIN media_assets ma ON ma.id = mm.media_asset_id AND ma.status = 'READY'
                    WHERE mm.merchant_id = m.id
                      AND mm.role IN ('LOGO', 'PRIMARY', 'ICON')
                      AND (mm.valid_until IS NULL OR mm.valid_until > NOW())
                    ORDER BY CASE mm.role WHEN 'LOGO' THEN 0 WHEN 'PRIMARY' THEN 1 ELSE 2 END,
                             mm.is_primary DESC
                    LIMIT 1
                ) logo ON TRUE
                WHERE m.id = $1
                """,
                merchant_id,
            )
        if row is None:
            return None
        return self._entry_from_row(row)

    async def get_by_code(self, merchant_code: str) -> Optional[MerchantDirectoryEntry]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT m.id, m.merchant_code, m.display_name, m.status,
                       logo.cdn_url AS logo_cdn_url
                FROM merchants m
                LEFT JOIN LATERAL (
                    SELECT ma.cdn_url
                    FROM merchant_media mm
                    JOIN media_assets ma ON ma.id = mm.media_asset_id AND ma.status = 'READY'
                    WHERE mm.merchant_id = m.id
                      AND mm.role IN ('LOGO', 'PRIMARY', 'ICON')
                      AND (mm.valid_until IS NULL OR mm.valid_until > NOW())
                    ORDER BY CASE mm.role WHEN 'LOGO' THEN 0 WHEN 'PRIMARY' THEN 1 ELSE 2 END,
                             mm.is_primary DESC
                    LIMIT 1
                ) logo ON TRUE
                WHERE m.merchant_code = $1
                """,
                merchant_code,
            )
        if row is None:
            return None
        return self._entry_from_row(row)

    @staticmethod
    def _entry_from_row(row: Any) -> MerchantDirectoryEntry:
        return MerchantDirectoryEntry(
            id=int(row["id"]),
            merchant_code=str(row["merchant_code"]),
            display_name=str(row["display_name"]),
            status=str(row["status"]),
            logo_cdn_url=str(row["logo_cdn_url"]) if row["logo_cdn_url"] else None,
        )

    async def get_display_name(self, merchant_id: int) -> Optional[str]:
        entry = await self.get(merchant_id)
        if entry is None or entry.status != "ACTIVE":
            return None
        return entry.display_name

    async def get_logo_cdn_url(self, merchant_id: int) -> Optional[str]:
        entry = await self.get(merchant_id)
        if entry is None or entry.status != "ACTIVE":
            return None
        return entry.logo_cdn_url

    async def upsert(self, entry: MerchantDirectoryEntry) -> MerchantDirectoryEntry:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO merchants (merchant_code, display_name, status)
                VALUES ($1,$2,$3)
                ON CONFLICT (merchant_code) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    status = EXCLUDED.status,
                    updated_at = NOW()
                RETURNING id, merchant_code, display_name, status
                """,
                entry.merchant_code,
                entry.display_name,
                entry.status,
            )
        return MerchantDirectoryEntry(
            id=int(row["id"]),
            merchant_code=str(row["merchant_code"]),
            display_name=str(row["display_name"]),
            status=str(row["status"]),
            logo_cdn_url=entry.logo_cdn_url,
        )

    async def list_active(self, *, limit: int = 200) -> Sequence[MerchantDirectoryEntry]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.id, m.merchant_code, m.display_name, m.status,
                       logo.cdn_url AS logo_cdn_url
                FROM merchants m
                LEFT JOIN LATERAL (
                    SELECT ma.cdn_url
                    FROM merchant_media mm
                    JOIN media_assets ma ON ma.id = mm.media_asset_id AND ma.status = 'READY'
                    WHERE mm.merchant_id = m.id
                      AND mm.role IN ('LOGO', 'PRIMARY', 'ICON')
                    ORDER BY CASE mm.role WHEN 'LOGO' THEN 0 ELSE 1 END, mm.is_primary DESC
                    LIMIT 1
                ) logo ON TRUE
                WHERE m.status = 'ACTIVE'
                ORDER BY m.id ASC
                LIMIT $1
                """,
                limit,
            )
        return tuple(
            MerchantDirectoryEntry(
                id=int(r["id"]),
                merchant_code=str(r["merchant_code"]),
                display_name=str(r["display_name"]),
                status=str(r["status"]),
                logo_cdn_url=str(r["logo_cdn_url"]) if r["logo_cdn_url"] else None,
            )
            for r in rows
        )


async def resolve_merchant_display_name(
    merchant_id: int,
    directory: Optional[MerchantDirectory],
) -> str:
    """Prefer DB display_name; fall back to opaque id label (never invent brands)."""

    if directory is not None:
        name = await directory.get_display_name(merchant_id)
        if name:
            return name
    return f"merchant:{merchant_id}"


__all__ = [
    "InMemoryMerchantDirectory",
    "MerchantDirectory",
    "MerchantDirectoryEntry",
    "PostgresMerchantDirectory",
    "resolve_merchant_display_name",
]
