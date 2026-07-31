"""Postgres persistence for search sessions / events / LLM jobs (ADR-011 P1)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from taksitlio.search_sessions.repository import (
    QueryVersion,
    SearchSession,
    SearchTimeoutPolicy,
    SessionEvent,
)
from taksitlio.search_sessions.status import SearchSessionStatus, transition


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


class PostgresSearchSessionRepository:
    """asyncpg-backed store. Complements in-memory orchestrator runtime state."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self.policy = SearchTimeoutPolicy()

    async def load_timeout_policy(self, policy_code: str = "SEARCH_DEFAULT") -> SearchTimeoutPolicy:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM search_timeout_policies
                WHERE policy_code = $1 AND status = 'ACTIVE'
                """,
                policy_code,
            )
        if row is None:
            return SearchTimeoutPolicy()
        self.policy = SearchTimeoutPolicy(
            policy_code=row["policy_code"],
            queue_soft_deadline_ms=int(row["queue_soft_deadline_ms"]),
            inference_soft_deadline_ms=int(row["inference_soft_deadline_ms"]),
            partial_result_deadline_ms=int(row["partial_result_deadline_ms"]),
            ux_fallback_deadline_ms=int(row["ux_fallback_deadline_ms"]),
            hard_timeout_ms=int(row["hard_timeout_ms"]),
            max_clarifications_per_session=int(row["max_clarifications_per_session"]),
            max_clarifications_per_message=int(row["max_clarifications_per_message"]),
        )
        return self.policy

    async def create_session(
        self,
        *,
        conversation_id: str,
        message: str,
        client_query_id: Optional[str] = None,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> tuple[SearchSession, QueryVersion]:
        sid = uuid.uuid4()
        vid = uuid.uuid4()
        conv = uuid.UUID(conversation_id) if isinstance(conversation_id, str) else conversation_id
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO search_sessions (
                        id, conversation_id, user_id, organization_id, status,
                        active_query_version, client_query_id
                    ) VALUES ($1,$2,$3,$4,'RECEIVED',1,$5)
                    """,
                    sid,
                    conv,
                    uuid.UUID(user_id) if user_id else None,
                    uuid.UUID(organization_id) if organization_id else None,
                    uuid.UUID(client_query_id) if client_query_id else None,
                )
                await conn.execute(
                    """
                    INSERT INTO search_query_versions (
                        id, search_session_id, version_number, raw_user_text, normalized_text
                    ) VALUES ($1,$2,1,$3,$4)
                    """,
                    vid,
                    sid,
                    message,
                    message.strip().lower(),
                )
                await conn.execute(
                    """
                    INSERT INTO search_session_messages (
                        search_session_id, query_version, role, content
                    ) VALUES ($1,1,'USER',$2)
                    """,
                    sid,
                    message,
                )
        session = SearchSession(
            id=str(sid),
            conversation_id=str(conv),
            status=SearchSessionStatus.RECEIVED,
            user_id=user_id,
            organization_id=organization_id,
            client_query_id=client_query_id,
        )
        version = QueryVersion(
            id=str(vid),
            search_session_id=str(sid),
            version_number=1,
            raw_user_text=message,
            normalized_text=message.strip().lower(),
        )
        return session, version

    async def set_status(self, session_id: str, target: SearchSessionStatus) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM search_sessions WHERE id = $1::uuid",
                session_id,
            )
            if row is None:
                raise KeyError("search_session_not_found")
            current = SearchSessionStatus(row["status"])
            next_status = transition(current, target)
            completed = next_status in {
                SearchSessionStatus.COMPLETED,
                SearchSessionStatus.COMPLETED_DEGRADED,
                SearchSessionStatus.FAILED,
            }
            cancelled = next_status == SearchSessionStatus.CANCELLED
            await conn.execute(
                """
                UPDATE search_sessions SET
                    status = $2,
                    completed_at = CASE WHEN $3 THEN NOW() ELSE completed_at END,
                    cancelled_at = CASE WHEN $4 THEN NOW() ELSE cancelled_at END,
                    updated_at = NOW()
                WHERE id = $1::uuid
                """,
                session_id,
                next_status.value,
                completed,
                cancelled,
            )

    async def append_query_version(self, session_id: str, *, raw_user_text: str) -> QueryVersion:
        vid = uuid.uuid4()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT active_query_version FROM search_sessions WHERE id = $1::uuid FOR UPDATE",
                    session_id,
                )
                if row is None:
                    raise KeyError("search_session_not_found")
                next_n = int(row["active_query_version"]) + 1
                await conn.execute(
                    """
                    INSERT INTO search_query_versions (
                        id, search_session_id, version_number, raw_user_text, normalized_text
                    ) VALUES ($1,$2::uuid,$3,$4,$5)
                    """,
                    vid,
                    session_id,
                    next_n,
                    raw_user_text,
                    raw_user_text.strip().lower(),
                )
                await conn.execute(
                    """
                    UPDATE search_sessions
                    SET active_query_version = $2, updated_at = NOW()
                    WHERE id = $1::uuid
                    """,
                    session_id,
                    next_n,
                )
        return QueryVersion(
            id=str(vid),
            search_session_id=session_id,
            version_number=next_n,
            raw_user_text=raw_user_text,
            normalized_text=raw_user_text.strip().lower(),
        )

    async def append_event(
        self,
        session_id: str,
        *,
        query_version: int,
        event_type: str,
        display_message: Optional[str] = None,
        data_origin: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        severity: str = "INFO",
        event_id: Optional[str] = None,
    ) -> SessionEvent:
        eid = uuid.UUID(event_id) if event_id else uuid.uuid4()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO search_session_events (
                    id, search_session_id, query_version, event_type, severity,
                    display_message, data_origin, payload
                ) VALUES ($1,$2::uuid,$3,$4,$5,$6,$7,$8::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                eid,
                session_id,
                query_version,
                event_type,
                severity,
                display_message,
                data_origin,
                _json(payload or {}),
            )
        return SessionEvent(
            id=str(eid),
            search_session_id=session_id,
            query_version=query_version,
            event_type=event_type,
            severity=severity,
            display_message=display_message,
            data_origin=data_origin,
            payload=dict(payload or {}),
        )

    async def list_events(
        self,
        session_id: str,
        *,
        after_id: Optional[str] = None,
        limit: int = 200,
    ) -> list[SessionEvent]:
        async with self._pool.acquire() as conn:
            if after_id:
                rows = await conn.fetch(
                    """
                    SELECT * FROM search_session_events
                    WHERE search_session_id = $1::uuid
                      AND created_at > (
                          SELECT created_at FROM search_session_events WHERE id = $2::uuid
                      )
                    ORDER BY created_at ASC
                    LIMIT $3
                    """,
                    session_id,
                    after_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM search_session_events
                    WHERE search_session_id = $1::uuid
                    ORDER BY created_at ASC
                    LIMIT $2
                    """,
                    session_id,
                    limit,
                )
        return [
            SessionEvent(
                id=str(r["id"]),
                search_session_id=str(r["search_session_id"]),
                query_version=int(r["query_version"]),
                event_type=r["event_type"],
                severity=r["severity"],
                display_message=r["display_message"],
                data_origin=r["data_origin"],
                payload=dict(r["payload"] or {}),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def upsert_llm_job(self, job: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_understanding_jobs (
                    id, search_session_id, query_version, conversation_state_version,
                    status, platform_role, input_payload, output_payload, error_code,
                    queued_at, started_at, completed_at
                ) VALUES (
                    $1::uuid,$2::uuid,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,
                    COALESCE($10::timestamptz, NOW()), $11::timestamptz, $12::timestamptz
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    output_payload = EXCLUDED.output_payload,
                    error_code = EXCLUDED.error_code,
                    started_at = COALESCE(EXCLUDED.started_at, llm_understanding_jobs.started_at),
                    completed_at = COALESCE(EXCLUDED.completed_at, llm_understanding_jobs.completed_at),
                    updated_at = NOW()
                """,
                job["id"],
                job["search_session_id"],
                int(job["query_version"]),
                int(job.get("conversation_state_version") or 0),
                job["status"],
                job.get("platform_role") or "UNDERSTANDING_SERVICE",
                _json(job.get("input_payload") or {}),
                _json(job.get("output_payload")) if job.get("output_payload") is not None else None,
                job.get("error_code"),
                job.get("queued_at"),
                job.get("started_at"),
                job.get("completed_at"),
            )

    async def claim_queued_jobs(self, *, limit: int = 5) -> list[dict[str, Any]]:
        """SKIP LOCKED claim for async worker."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH cte AS (
                    SELECT id FROM llm_understanding_jobs
                    WHERE status = 'QUEUED'
                    ORDER BY queued_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1
                )
                UPDATE llm_understanding_jobs j
                SET status = 'RUNNING', started_at = NOW(), updated_at = NOW()
                FROM cte WHERE j.id = cte.id
                RETURNING j.*
                """,
                limit,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            payload = r["input_payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            out.append(
                {
                    "id": str(r["id"]),
                    "search_session_id": str(r["search_session_id"]),
                    "query_version": int(r["query_version"]),
                    "conversation_state_version": int(r["conversation_state_version"]),
                    "status": r["status"],
                    "input_payload": dict(payload or {}),
                    "platform_role": r["platform_role"],
                }
            )
        return out

    async def record_metric(
        self,
        session_id: str,
        name: str,
        value: float,
        *,
        labels: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO search_session_metrics (
                    search_session_id, metric_name, metric_value, labels
                ) VALUES ($1::uuid,$2,$3,$4::jsonb)
                """,
                session_id,
                name,
                value,
                _json(labels or {}),
            )

    async def get_session(self, session_id: str) -> Optional[SearchSession]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM search_sessions WHERE id = $1::uuid",
                session_id,
            )
        if row is None:
            return None
        return self._row_to_session(row)

    def _row_to_session(self, row: Any) -> SearchSession:
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        return SearchSession(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            status=SearchSessionStatus(row["status"]),
            active_query_version=int(row["active_query_version"]),
            clarification_count=int(row["clarification_count"]),
            user_id=str(row["user_id"]) if row["user_id"] else None,
            organization_id=str(row["organization_id"]) if row["organization_id"] else None,
            client_query_id=str(row["client_query_id"]) if row["client_query_id"] else None,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
            superseded_by=str(row["superseded_by"]) if row["superseded_by"] else None,
            metadata=dict(meta or {}),
        )

    async def load_full_session(self, session_id: str) -> Optional[Any]:
        """Load session + versions/events/jobs/partials/clarifications for hydrate."""

        from taksitlio.search_sessions.hydrate import SessionSnapshot

        session = await self.get_session(session_id)
        if session is None:
            return None
        async with self._pool.acquire() as conn:
            version_rows = await conn.fetch(
                """
                SELECT * FROM search_query_versions
                WHERE search_session_id = $1::uuid
                ORDER BY version_number ASC
                """,
                session_id,
            )
            event_rows = await conn.fetch(
                """
                SELECT * FROM search_session_events
                WHERE search_session_id = $1::uuid
                ORDER BY created_at ASC
                """,
                session_id,
            )
            clar_rows = await conn.fetch(
                """
                SELECT * FROM clarification_requests
                WHERE search_session_id = $1::uuid
                ORDER BY created_at ASC
                """,
                session_id,
            )
            partial_rows = await conn.fetch(
                """
                SELECT * FROM partial_result_snapshots
                WHERE search_session_id = $1::uuid
                ORDER BY created_at ASC
                """,
                session_id,
            )
            job_rows = await conn.fetch(
                """
                SELECT * FROM llm_understanding_jobs
                WHERE search_session_id = $1::uuid
                ORDER BY queued_at ASC
                """,
                session_id,
            )

        versions = [
            QueryVersion(
                id=str(r["id"]),
                search_session_id=str(r["search_session_id"]),
                version_number=int(r["version_number"]),
                raw_user_text=r["raw_user_text"],
                normalized_text=r["normalized_text"],
                state_snapshot=dict(r["state_snapshot"] or {})
                if not isinstance(r["state_snapshot"], str)
                else json.loads(r["state_snapshot"] or "{}"),
                confidence=float(r["confidence"]) if r["confidence"] is not None else None,
                requires_llm=bool(r["requires_llm"]),
                created_at=r["created_at"],
            )
            for r in version_rows
        ]
        events = [
            SessionEvent(
                id=str(r["id"]),
                search_session_id=str(r["search_session_id"]),
                query_version=int(r["query_version"]),
                event_type=r["event_type"],
                severity=r["severity"],
                display_message=r["display_message"],
                data_origin=r["data_origin"],
                payload=dict(r["payload"] or {})
                if not isinstance(r["payload"], str)
                else json.loads(r["payload"] or "{}"),
                created_at=r["created_at"],
            )
            for r in event_rows
        ]
        clarifications = [
            {
                "clarification_id": str(r["id"]),
                "query_version": int(r["query_version"]),
                "field": r["field"],
                "question_text": r["question_text"],
                "question_signature": r["question_signature"],
                "options": list(r["options"] or [])
                if not isinstance(r["options"], str)
                else json.loads(r["options"] or "[]"),
                "status": r["status"],
            }
            for r in clar_rows
        ]
        partials = []
        for r in partial_rows:
            ranking = r["ranking_payload"]
            if isinstance(ranking, str):
                ranking = json.loads(ranking or "{}")
            ranking = dict(ranking or {})
            ranking.setdefault("query_version", int(r["query_version"]))
            ranking.setdefault("label", r["label"])
            partials.append(ranking)
        llm_jobs = []
        for r in job_rows:
            inp = r["input_payload"]
            out = r["output_payload"]
            if isinstance(inp, str):
                inp = json.loads(inp or "{}")
            if isinstance(out, str):
                out = json.loads(out) if out else None
            llm_jobs.append(
                {
                    "id": str(r["id"]),
                    "search_session_id": str(r["search_session_id"]),
                    "query_version": int(r["query_version"]),
                    "conversation_state_version": int(r["conversation_state_version"]),
                    "status": r["status"],
                    "input_payload": dict(inp or {}),
                    "output_payload": dict(out) if out is not None else None,
                    "error_code": r["error_code"],
                    "queued_at": r["queued_at"],
                    "started_at": r["started_at"],
                    "completed_at": r["completed_at"],
                }
            )
        return SessionSnapshot(
            session=session,
            versions=versions,
            events=events,
            clarifications=clarifications,
            partials=partials,
            llm_jobs=llm_jobs,
            metadata=dict(session.metadata or {}),
        )

    async def upsert_partial_snapshot(
        self,
        *,
        session_id: str,
        query_version: int,
        label: str,
        product_ids: list[Any],
        ranking_payload: dict[str, Any],
        snapshot_id: Optional[str] = None,
    ) -> str:
        sid = snapshot_id or str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"partial:{session_id}:{query_version}:{label}")
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO partial_result_snapshots (
                    id, search_session_id, query_version, label, product_ids, ranking_payload
                ) VALUES ($1::uuid,$2::uuid,$3,$4,$5::jsonb,$6::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    product_ids = EXCLUDED.product_ids,
                    ranking_payload = EXCLUDED.ranking_payload,
                    label = EXCLUDED.label
                """,
                sid,
                session_id,
                query_version,
                label,
                _json([p for p in product_ids if p is not None]),
                _json(ranking_payload),
            )
        return sid

    async def upsert_clarification_request(
        self,
        *,
        clarification_id: str,
        session_id: str,
        query_version: int,
        field: str,
        question_text: str,
        question_signature: str,
        options: list[Any],
        status: str = "PENDING",
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO clarification_requests (
                    id, search_session_id, query_version, field, question_text,
                    question_signature, options, status
                ) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7::jsonb,$8)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    options = EXCLUDED.options
                """,
                clarification_id,
                session_id,
                query_version,
                field,
                question_text,
                question_signature,
                _json(options),
                status,
            )

    async def upsert_session_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE search_sessions
                SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
                    updated_at = NOW()
                WHERE id = $1::uuid
                """,
                session_id,
                _json(metadata),
            )

    async def list_recent_metrics(self, *, limit: int = 500) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT metric_name, metric_value, labels, recorded_at
                FROM search_session_metrics
                ORDER BY recorded_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [
            {
                "metric_name": r["metric_name"],
                "metric_value": float(r["metric_value"]),
                "labels": dict(r["labels"] or {}),
                "recorded_at": r["recorded_at"].isoformat() if r["recorded_at"] else None,
            }
            for r in rows
        ]
