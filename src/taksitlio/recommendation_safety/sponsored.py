"""Sponsored placement registry (ADR-012 §25) — never steals organic 'en uygun'."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence


@dataclass(frozen=True)
class SponsoredPlacementRecord:
    product_id: str
    weight: float = 0.0
    merchant_id: Optional[str] = None
    active: bool = True
    label: str = "sponsored"


class SponsoredPlacementStore(Protocol):
    def list_active(self) -> tuple[SponsoredPlacementRecord, ...]: ...

    def upsert(self, record: SponsoredPlacementRecord) -> None: ...

    def deactivate(self, product_id: str) -> None: ...


@dataclass
class InMemorySponsoredPlacementStore:
    placements: dict[str, SponsoredPlacementRecord] = field(default_factory=dict)

    def list_active(self) -> tuple[SponsoredPlacementRecord, ...]:
        return tuple(p for p in self.placements.values() if p.active)

    def upsert(self, record: SponsoredPlacementRecord) -> None:
        self.placements[record.product_id] = record

    def deactivate(self, product_id: str) -> None:
        prev = self.placements.get(product_id)
        if prev is None:
            return
        self.placements[product_id] = SponsoredPlacementRecord(
            product_id=prev.product_id,
            weight=prev.weight,
            merchant_id=prev.merchant_id,
            active=False,
            label=prev.label,
        )

    def as_search_kwargs(self) -> dict[str, Any]:
        active = self.list_active()
        return {
            "sponsored_product_ids": tuple(p.product_id for p in active),
            "sponsored_weights": {p.product_id: float(p.weight) for p in active},
        }


@dataclass
class PostgresSponsoredPlacementStore(InMemorySponsoredPlacementStore):
    """V024 sponsored_placements table; dual-writes with in-memory cache."""

    pool: Any = None

    async def hydrate(self) -> None:
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT product_id, weight, merchant_id, is_active, label
                FROM sponsored_placements
                WHERE is_active = TRUE
                """
            )
        for row in rows:
            self.upsert(
                SponsoredPlacementRecord(
                    product_id=str(row["product_id"]),
                    weight=float(row["weight"] or 0),
                    merchant_id=(
                        str(row["merchant_id"]) if row["merchant_id"] is not None else None
                    ),
                    active=bool(row["is_active"]),
                    label=str(row["label"] or "sponsored"),
                )
            )

    async def upsert_async(self, record: SponsoredPlacementRecord) -> None:
        self.upsert(record)
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sponsored_placements
                    (product_id, weight, merchant_id, is_active, label)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (product_id) DO UPDATE SET
                    weight = EXCLUDED.weight,
                    merchant_id = EXCLUDED.merchant_id,
                    is_active = EXCLUDED.is_active,
                    label = EXCLUDED.label,
                    updated_at = NOW()
                """,
                record.product_id,
                float(record.weight),
                record.merchant_id,
                bool(record.active),
                record.label,
            )

    async def deactivate_async(self, product_id: str) -> None:
        self.deactivate(product_id)
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sponsored_placements
                SET is_active = FALSE, updated_at = NOW()
                WHERE product_id = $1
                """,
                product_id,
            )


def placements_from_mapping(raw: Sequence[Mapping[str, Any]]) -> tuple[SponsoredPlacementRecord, ...]:
    out: list[SponsoredPlacementRecord] = []
    for item in raw:
        pid = str(item.get("product_id") or "").strip()
        if not pid:
            continue
        out.append(
            SponsoredPlacementRecord(
                product_id=pid,
                weight=float(item.get("weight") or 0),
                merchant_id=(
                    str(item["merchant_id"]) if item.get("merchant_id") is not None else None
                ),
                active=bool(item.get("active", True)),
                label=str(item.get("label") or "sponsored"),
            )
        )
    return tuple(out)


__all__ = [
    "InMemorySponsoredPlacementStore",
    "PostgresSponsoredPlacementStore",
    "SponsoredPlacementRecord",
    "SponsoredPlacementStore",
    "placements_from_mapping",
]
