"""Ingestion source/run repository — in-memory + Postgres (ADR-010 P7)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Sequence

from taksitlio.ingestion.store import (
    CreateSourceInput,
    IngestionRunItemRecord,
    IngestionRunRecord,
    IngestionSourceRecord,
    PersistRunInput,
    SourceHealthRecord,
)


class IngestionRepository(Protocol):
    async def upsert_source(self, data: CreateSourceInput) -> IngestionSourceRecord: ...

    async def get_source_by_code(self, source_code: str) -> Optional[IngestionSourceRecord]: ...

    async def list_sources(self, *, limit: int = 100) -> Sequence[IngestionSourceRecord]: ...

    async def persist_run(self, data: PersistRunInput) -> IngestionRunRecord: ...

    async def list_runs(
        self, *, source_id: Optional[int] = None, limit: int = 50
    ) -> Sequence[IngestionRunRecord]: ...

    async def upsert_health(self, record: SourceHealthRecord) -> SourceHealthRecord: ...

    async def list_health(self) -> Sequence[SourceHealthRecord]: ...


class InMemoryIngestionRepository:
    def __init__(self) -> None:
        self._sources: dict[int, IngestionSourceRecord] = {}
        self._by_code: dict[str, int] = {}
        self._runs: dict[int, IngestionRunRecord] = {}
        self._items: dict[int, list[IngestionRunItemRecord]] = {}
        self._health: dict[int, SourceHealthRecord] = {}
        self._next_source = 1
        self._next_run = 1
        self._next_item = 1

    async def upsert_source(self, data: CreateSourceInput) -> IngestionSourceRecord:
        existing_id = self._by_code.get(data.source_code)
        if existing_id is not None:
            prev = self._sources[existing_id]
            rec = IngestionSourceRecord(
                id=prev.id,
                merchant_id=data.merchant_id,
                source_code=data.source_code,
                source_type=data.source_type,
                adapter_code=data.adapter_code,
                status=data.status,
                priority=data.priority,
                credential_ref=data.credential_ref,
                base_url=data.base_url,
                consecutive_failures=prev.consecutive_failures,
                last_success_at=prev.last_success_at,
                last_failure_at=prev.last_failure_at,
                metadata=dict(data.metadata),
            )
            self._sources[existing_id] = rec
            return rec
        sid = self._next_source
        self._next_source += 1
        rec = IngestionSourceRecord(
            id=sid,
            merchant_id=data.merchant_id,
            source_code=data.source_code,
            source_type=data.source_type,
            adapter_code=data.adapter_code,
            status=data.status,
            priority=data.priority,
            credential_ref=data.credential_ref,
            base_url=data.base_url,
            metadata=dict(data.metadata),
        )
        self._sources[sid] = rec
        self._by_code[data.source_code] = sid
        return rec

    async def get_source_by_code(self, source_code: str) -> Optional[IngestionSourceRecord]:
        sid = self._by_code.get(source_code)
        return None if sid is None else self._sources.get(sid)

    async def list_sources(self, *, limit: int = 100) -> Sequence[IngestionSourceRecord]:
        rows = sorted(self._sources.values(), key=lambda s: s.id)
        return tuple(rows[:limit])

    async def persist_run(self, data: PersistRunInput) -> IngestionRunRecord:
        now = datetime.now(timezone.utc)
        rid = self._next_run
        self._next_run += 1
        run = IngestionRunRecord(
            id=rid,
            source_id=data.source_id,
            run_type=data.run_type,
            status=data.status,
            items_discovered=data.items_discovered,
            items_changed=data.items_changed,
            items_skipped=data.items_skipped,
            items_failed=data.items_failed,
            started_at=now,
            finished_at=now,
            error_code=data.error_code,
            error_summary=data.error_summary,
            metadata=dict(data.metadata),
        )
        self._runs[rid] = run
        item_rows: list[IngestionRunItemRecord] = []
        for raw in data.items:
            iid = self._next_item
            self._next_item += 1
            item_rows.append(
                IngestionRunItemRecord(
                    id=iid,
                    run_id=rid,
                    action=str(raw.get("action") or "DISCOVERED"),
                    external_item_id=raw.get("external_item_id"),
                    item_kind=str(raw.get("item_kind") or "PRODUCT"),
                    content_hash=raw.get("content_hash"),
                    error_code=raw.get("error_code"),
                    error_detail=raw.get("error_detail"),
                    source_reference=raw.get("source_reference"),
                )
            )
        self._items[rid] = item_rows

        source = self._sources.get(data.source_id)
        if source is not None:
            if data.status in {"SUCCEEDED", "PARTIAL"}:
                self._sources[data.source_id] = IngestionSourceRecord(
                    **{
                        **source.__dict__,
                        "last_success_at": now,
                        "consecutive_failures": 0
                        if data.status == "SUCCEEDED"
                        else source.consecutive_failures,
                    }
                )
            elif data.status == "FAILED":
                self._sources[data.source_id] = IngestionSourceRecord(
                    **{
                        **source.__dict__,
                        "last_failure_at": now,
                        "consecutive_failures": source.consecutive_failures + 1,
                    }
                )
        return run

    async def list_runs(
        self, *, source_id: Optional[int] = None, limit: int = 50
    ) -> Sequence[IngestionRunRecord]:
        rows = list(self._runs.values())
        if source_id is not None:
            rows = [r for r in rows if r.source_id == source_id]
        rows.sort(key=lambda r: r.id, reverse=True)
        return tuple(rows[:limit])

    async def upsert_health(self, record: SourceHealthRecord) -> SourceHealthRecord:
        self._health[record.source_id] = record
        return record

    async def list_health(self) -> Sequence[SourceHealthRecord]:
        return tuple(sorted(self._health.values(), key=lambda h: h.source_id))

    def run_items(self, run_id: int) -> Sequence[IngestionRunItemRecord]:
        return tuple(self._items.get(run_id, ()))


class PostgresIngestionRepository:
    """asyncpg-backed repository. Pool must be an asyncpg.Pool."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def upsert_source(self, data: CreateSourceInput) -> IngestionSourceRecord:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO ingestion_sources (
                    merchant_id, source_code, source_type, adapter_code,
                    status, priority, credential_ref, base_url, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                ON CONFLICT (source_code) DO UPDATE SET
                    merchant_id = EXCLUDED.merchant_id,
                    source_type = EXCLUDED.source_type,
                    adapter_code = EXCLUDED.adapter_code,
                    status = EXCLUDED.status,
                    priority = EXCLUDED.priority,
                    credential_ref = EXCLUDED.credential_ref,
                    base_url = EXCLUDED.base_url,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING *
                """,
                data.merchant_id,
                data.source_code,
                data.source_type,
                data.adapter_code,
                data.status,
                data.priority,
                data.credential_ref,
                data.base_url,
                dict(data.metadata),
            )
        return _source_from_row(row)

    async def get_source_by_code(self, source_code: str) -> Optional[IngestionSourceRecord]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ingestion_sources WHERE source_code = $1",
                source_code,
            )
        return None if row is None else _source_from_row(row)

    async def list_sources(self, *, limit: int = 100) -> Sequence[IngestionSourceRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM ingestion_sources ORDER BY id ASC LIMIT $1",
                limit,
            )
        return tuple(_source_from_row(r) for r in rows)

    async def persist_run(self, data: PersistRunInput) -> IngestionRunRecord:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO ingestion_runs (
                        source_id, run_type, status, started_at, finished_at,
                        items_discovered, items_changed, items_skipped, items_failed,
                        error_code, error_summary, metadata
                    ) VALUES (
                        $1,$2,$3,NOW(),NOW(),$4,$5,$6,$7,$8,$9,$10::jsonb
                    )
                    RETURNING *
                    """,
                    data.source_id,
                    data.run_type,
                    data.status,
                    data.items_discovered,
                    data.items_changed,
                    data.items_skipped,
                    data.items_failed,
                    data.error_code,
                    data.error_summary,
                    dict(data.metadata),
                )
                run_id = int(row["id"])
                for raw in data.items:
                    await conn.execute(
                        """
                        INSERT INTO ingestion_run_items (
                            run_id, external_item_id, item_kind, content_hash,
                            action, error_code, error_detail, source_reference
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                        """,
                        run_id,
                        raw.get("external_item_id"),
                        str(raw.get("item_kind") or "PRODUCT"),
                        raw.get("content_hash"),
                        str(raw.get("action") or "DISCOVERED"),
                        raw.get("error_code"),
                        raw.get("error_detail"),
                        raw.get("source_reference"),
                    )
                if data.status in {"SUCCEEDED", "PARTIAL"}:
                    await conn.execute(
                        """
                        UPDATE ingestion_sources
                        SET last_success_at = NOW(),
                            consecutive_failures = CASE
                                WHEN $2 = 'SUCCEEDED' THEN 0
                                ELSE consecutive_failures
                            END,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        data.source_id,
                        data.status,
                    )
                elif data.status == "FAILED":
                    await conn.execute(
                        """
                        UPDATE ingestion_sources
                        SET last_failure_at = NOW(),
                            consecutive_failures = consecutive_failures + 1,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        data.source_id,
                    )
        return _run_from_row(row)

    async def list_runs(
        self, *, source_id: Optional[int] = None, limit: int = 50
    ) -> Sequence[IngestionRunRecord]:
        async with self._pool.acquire() as conn:
            if source_id is None:
                rows = await conn.fetch(
                    "SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT $1",
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM ingestion_runs
                    WHERE source_id = $1
                    ORDER BY id DESC LIMIT $2
                    """,
                    source_id,
                    limit,
                )
        return tuple(_run_from_row(r) for r in rows)

    async def upsert_health(self, record: SourceHealthRecord) -> SourceHealthRecord:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO source_health_status (
                    source_id, health, consecutive_failures,
                    last_check_at, last_success_at, last_failure_at, detail, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
                ON CONFLICT (source_id) DO UPDATE SET
                    health = EXCLUDED.health,
                    consecutive_failures = EXCLUDED.consecutive_failures,
                    last_check_at = EXCLUDED.last_check_at,
                    last_success_at = EXCLUDED.last_success_at,
                    last_failure_at = EXCLUDED.last_failure_at,
                    detail = EXCLUDED.detail,
                    updated_at = NOW()
                RETURNING *
                """,
                record.source_id,
                record.health,
                record.consecutive_failures,
                record.last_check_at,
                record.last_success_at,
                record.last_failure_at,
                record.detail,
            )
        return _health_from_row(row)

    async def list_health(self) -> Sequence[SourceHealthRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM source_health_status ORDER BY source_id ASC"
            )
        return tuple(_health_from_row(r) for r in rows)


def _meta(row: Any) -> dict:
    value = row["metadata"] if "metadata" in row.keys() else {}
    return dict(value or {})


def _source_from_row(row: Any) -> IngestionSourceRecord:
    return IngestionSourceRecord(
        id=int(row["id"]),
        merchant_id=int(row["merchant_id"]),
        source_code=str(row["source_code"]),
        source_type=str(row["source_type"]),
        adapter_code=str(row["adapter_code"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        credential_ref=row["credential_ref"],
        base_url=row["base_url"],
        consecutive_failures=int(row["consecutive_failures"] or 0),
        last_success_at=row["last_success_at"],
        last_failure_at=row["last_failure_at"],
        metadata=_meta(row),
    )


def _run_from_row(row: Any) -> IngestionRunRecord:
    return IngestionRunRecord(
        id=int(row["id"]),
        source_id=int(row["source_id"]),
        run_type=str(row["run_type"]),
        status=str(row["status"]),
        items_discovered=int(row["items_discovered"] or 0),
        items_changed=int(row["items_changed"] or 0),
        items_skipped=int(row["items_skipped"] or 0),
        items_failed=int(row["items_failed"] or 0),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_code=row["error_code"],
        error_summary=row["error_summary"],
        metadata=_meta(row),
    )


def _health_from_row(row: Any) -> SourceHealthRecord:
    return SourceHealthRecord(
        source_id=int(row["source_id"]),
        health=str(row["health"]),
        consecutive_failures=int(row["consecutive_failures"] or 0),
        last_check_at=row["last_check_at"],
        last_success_at=row["last_success_at"],
        last_failure_at=row["last_failure_at"],
        detail=row["detail"],
    )


__all__ = [
    "InMemoryIngestionRepository",
    "IngestionRepository",
    "PostgresIngestionRepository",
]
