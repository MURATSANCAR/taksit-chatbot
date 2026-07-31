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


class MerchantDirectory(Protocol):
    async def get(self, merchant_id: int) -> Optional[MerchantDirectoryEntry]: ...

    async def get_display_name(self, merchant_id: int) -> Optional[str]: ...

    async def upsert(self, entry: MerchantDirectoryEntry) -> MerchantDirectoryEntry: ...

    async def list_active(self, *, limit: int = 200) -> Sequence[MerchantDirectoryEntry]: ...


class InMemoryMerchantDirectory:
    def __init__(self, entries: Sequence[MerchantDirectoryEntry] = ()) -> None:
        self._by_id: dict[int, MerchantDirectoryEntry] = {e.id: e for e in entries}

    async def get(self, merchant_id: int) -> Optional[MerchantDirectoryEntry]:
        return self._by_id.get(merchant_id)

    async def get_display_name(self, merchant_id: int) -> Optional[str]:
        row = self._by_id.get(merchant_id)
        if row is None or row.status != "ACTIVE":
            return None
        return row.display_name

    async def upsert(self, entry: MerchantDirectoryEntry) -> MerchantDirectoryEntry:
        for existing in self._by_id.values():
            if existing.merchant_code == entry.merchant_code:
                updated = MerchantDirectoryEntry(
                    id=existing.id,
                    merchant_code=entry.merchant_code,
                    display_name=entry.display_name,
                    status=entry.status,
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
                SELECT id, merchant_code, display_name, status
                FROM merchants WHERE id = $1
                """,
                merchant_id,
            )
        if row is None:
            return None
        return MerchantDirectoryEntry(
            id=int(row["id"]),
            merchant_code=str(row["merchant_code"]),
            display_name=str(row["display_name"]),
            status=str(row["status"]),
        )

    async def get_display_name(self, merchant_id: int) -> Optional[str]:
        entry = await self.get(merchant_id)
        if entry is None or entry.status != "ACTIVE":
            return None
        return entry.display_name

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
        )

    async def list_active(self, *, limit: int = 200) -> Sequence[MerchantDirectoryEntry]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, merchant_code, display_name, status
                FROM merchants
                WHERE status = 'ACTIVE'
                ORDER BY id ASC
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
