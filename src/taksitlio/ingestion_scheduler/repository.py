"""Scheduler job persistence + lease (ADR-010 §63 / P7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Protocol, Sequence

from taksitlio.ingestion_scheduler.domain import SchedulerJobSpec, SchedulerQueue


@dataclass(frozen=True)
class SchedulerJobRecord:
    id: int
    queue_name: str
    priority: int
    status: str
    source_id: Optional[int] = None
    product_id: Optional[int] = None
    external_item_id: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_until: Optional[datetime] = None
    attempts: int = 0
    max_attempts: int = 5
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    available_at: Optional[datetime] = None


class SchedulerJobRepository(Protocol):
    async def enqueue(self, spec: SchedulerJobSpec) -> SchedulerJobRecord: ...

    async def lease_next(
        self,
        *,
        worker_id: str,
        queue_name: Optional[str] = None,
        lease_seconds: int = 60,
    ) -> Optional[SchedulerJobRecord]: ...

    async def mark_running(self, job_id: int) -> None: ...

    async def complete(self, job_id: int) -> None: ...

    async def fail(
        self,
        job_id: int,
        *,
        error_code: str,
        error_detail: Optional[str] = None,
        retry_delay_seconds: int = 30,
    ) -> None: ...

    async def list_jobs(
        self, *, status: Optional[str] = None, limit: int = 50
    ) -> Sequence[SchedulerJobRecord]: ...


def _payload(spec: SchedulerJobSpec) -> dict[str, Any]:
    return dict(spec.payload or {})


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class InMemorySchedulerJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[int, SchedulerJobRecord] = {}
        self._next = 1

    async def enqueue(self, spec: SchedulerJobSpec) -> SchedulerJobRecord:
        # Dedup active jobs with same queue+source+external
        source_id = _parse_optional_int(spec.source_id)
        for job in self._jobs.values():
            if (
                job.status in {"PENDING", "LEASED", "RUNNING"}
                and job.queue_name == spec.queue_name.value
                and job.source_id == source_id
                and job.external_item_id
                and job.external_item_id == spec.external_item_id
            ):
                return job
        jid = self._next
        self._next += 1
        now = datetime.now(timezone.utc)
        rec = SchedulerJobRecord(
            id=jid,
            queue_name=spec.queue_name.value,
            priority=spec.priority,
            status="PENDING",
            source_id=source_id,
            product_id=_parse_optional_int(spec.product_id),
            external_item_id=spec.external_item_id,
            payload=_payload(spec),
            available_at=now,
        )
        self._jobs[jid] = rec
        return rec

    async def lease_next(
        self,
        *,
        worker_id: str,
        queue_name: Optional[str] = None,
        lease_seconds: int = 60,
    ) -> Optional[SchedulerJobRecord]:
        now = datetime.now(timezone.utc)
        candidates: list[SchedulerJobRecord] = []
        for job in self._jobs.values():
            if queue_name and job.queue_name != queue_name:
                continue
            available = job.available_at or now
            if job.status == "PENDING" and available <= now:
                candidates.append(job)
            elif (
                job.status == "LEASED"
                and job.lease_until is not None
                and job.lease_until < now
            ):
                candidates.append(job)
        if not candidates:
            return None
        candidates.sort(key=lambda j: (j.priority, j.id))
        job = candidates[0]
        leased = SchedulerJobRecord(
            id=job.id,
            queue_name=job.queue_name,
            priority=job.priority,
            status="LEASED",
            source_id=job.source_id,
            product_id=job.product_id,
            external_item_id=job.external_item_id,
            lease_owner=worker_id,
            lease_until=now + timedelta(seconds=lease_seconds),
            attempts=job.attempts + 1,
            max_attempts=job.max_attempts,
            error_code=job.error_code,
            error_detail=job.error_detail,
            payload=dict(job.payload),
            available_at=job.available_at,
        )
        self._jobs[job.id] = leased
        return leased

    async def mark_running(self, job_id: int) -> None:
        job = self._jobs[job_id]
        self._jobs[job_id] = SchedulerJobRecord(
            **{**job.__dict__, "status": "RUNNING"}
        )

    async def complete(self, job_id: int) -> None:
        job = self._jobs[job_id]
        self._jobs[job_id] = SchedulerJobRecord(
            **{
                **job.__dict__,
                "status": "SUCCEEDED",
                "lease_owner": None,
                "lease_until": None,
            }
        )

    async def fail(
        self,
        job_id: int,
        *,
        error_code: str,
        error_detail: Optional[str] = None,
        retry_delay_seconds: int = 30,
    ) -> None:
        job = self._jobs[job_id]
        now = datetime.now(timezone.utc)
        if job.attempts >= job.max_attempts:
            status = "FAILED"
            available = job.available_at
        else:
            status = "PENDING"
            available = now + timedelta(seconds=retry_delay_seconds)
        self._jobs[job_id] = SchedulerJobRecord(
            id=job.id,
            queue_name=job.queue_name,
            priority=job.priority,
            status=status,
            source_id=job.source_id,
            product_id=job.product_id,
            external_item_id=job.external_item_id,
            lease_owner=None,
            lease_until=None,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error_code=error_code,
            error_detail=error_detail,
            payload=dict(job.payload),
            available_at=available,
        )

    async def list_jobs(
        self, *, status: Optional[str] = None, limit: int = 50
    ) -> Sequence[SchedulerJobRecord]:
        rows = list(self._jobs.values())
        if status:
            rows = [r for r in rows if r.status == status]
        rows.sort(key=lambda r: r.id, reverse=True)
        return tuple(rows[:limit])


class PostgresSchedulerJobRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def enqueue(self, spec: SchedulerJobSpec) -> SchedulerJobRecord:
        source_id = _parse_optional_int(spec.source_id)
        product_id = _parse_optional_int(spec.product_id)
        async with self._pool.acquire() as conn:
            if spec.external_item_id:
                existing = await conn.fetchrow(
                    """
                    SELECT * FROM ingestion_scheduler_jobs
                    WHERE queue_name = $1
                      AND source_id IS NOT DISTINCT FROM $2
                      AND external_item_id = $3
                      AND status IN ('PENDING', 'LEASED', 'RUNNING')
                    LIMIT 1
                    """,
                    spec.queue_name.value,
                    source_id,
                    spec.external_item_id,
                )
                if existing is not None:
                    return _job_from_row(existing)
            row = await conn.fetchrow(
                """
                INSERT INTO ingestion_scheduler_jobs (
                    queue_name, source_id, product_id, external_item_id,
                    priority, status, payload
                ) VALUES ($1,$2,$3,$4,$5,'PENDING',$6::jsonb)
                RETURNING *
                """,
                spec.queue_name.value,
                source_id,
                product_id,
                spec.external_item_id,
                spec.priority,
                _payload(spec),
            )
        return _job_from_row(row)

    async def lease_next(
        self,
        *,
        worker_id: str,
        queue_name: Optional[str] = None,
        lease_seconds: int = 60,
    ) -> Optional[SchedulerJobRecord]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM ingestion_scheduler_jobs
                        WHERE ($1::text IS NULL OR queue_name = $1)
                          AND (
                            (status = 'PENDING' AND available_at <= NOW())
                            OR (status = 'LEASED' AND lease_until < NOW())
                          )
                        ORDER BY priority ASC, available_at ASC, id ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE ingestion_scheduler_jobs j
                    SET status = 'LEASED',
                        lease_owner = $2,
                        lease_until = NOW() + make_interval(secs => $3),
                        attempts = attempts + 1,
                        updated_at = NOW()
                    FROM candidate
                    WHERE j.id = candidate.id
                    RETURNING j.*
                    """,
                    queue_name,
                    worker_id,
                    lease_seconds,
                )
        return None if row is None else _job_from_row(row)

    async def mark_running(self, job_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_scheduler_jobs
                SET status = 'RUNNING', updated_at = NOW()
                WHERE id = $1
                """,
                job_id,
            )

    async def complete(self, job_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_scheduler_jobs
                SET status = 'SUCCEEDED',
                    lease_owner = NULL,
                    lease_until = NULL,
                    updated_at = NOW()
                WHERE id = $1
                """,
                job_id,
            )

    async def fail(
        self,
        job_id: int,
        *,
        error_code: str,
        error_detail: Optional[str] = None,
        retry_delay_seconds: int = 30,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_scheduler_jobs
                SET status = CASE
                        WHEN attempts >= max_attempts THEN 'FAILED'
                        ELSE 'PENDING'
                    END,
                    lease_owner = NULL,
                    lease_until = NULL,
                    error_code = $2,
                    error_detail = $3,
                    available_at = CASE
                        WHEN attempts >= max_attempts THEN available_at
                        ELSE NOW() + make_interval(secs => $4)
                    END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                job_id,
                error_code,
                error_detail,
                retry_delay_seconds,
            )

    async def list_jobs(
        self, *, status: Optional[str] = None, limit: int = 50
    ) -> Sequence[SchedulerJobRecord]:
        async with self._pool.acquire() as conn:
            if status is None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM ingestion_scheduler_jobs
                    ORDER BY id DESC LIMIT $1
                    """,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM ingestion_scheduler_jobs
                    WHERE status = $1
                    ORDER BY id DESC LIMIT $2
                    """,
                    status,
                    limit,
                )
        return tuple(_job_from_row(r) for r in rows)


def _job_from_row(row: Any) -> SchedulerJobRecord:
    payload = row["payload"]
    return SchedulerJobRecord(
        id=int(row["id"]),
        queue_name=str(row["queue_name"]),
        priority=int(row["priority"]),
        status=str(row["status"]),
        source_id=None if row["source_id"] is None else int(row["source_id"]),
        product_id=None if row["product_id"] is None else int(row["product_id"]),
        external_item_id=row["external_item_id"],
        lease_owner=row["lease_owner"],
        lease_until=row["lease_until"],
        attempts=int(row["attempts"] or 0),
        max_attempts=int(row["max_attempts"] or 5),
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        payload=dict(payload or {}),
        available_at=row["available_at"],
    )


__all__ = [
    "InMemorySchedulerJobRepository",
    "PostgresSchedulerJobRepository",
    "SchedulerJobRecord",
    "SchedulerJobRepository",
]
