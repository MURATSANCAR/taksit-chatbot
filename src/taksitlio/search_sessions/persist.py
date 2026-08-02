"""Dual-write / snapshot persist of search session runtime → Postgres (ADR-011 P2)."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from taksitlio.search_sessions.orchestrator import SearchOrchestrator
from taksitlio.search_sessions.postgres import PostgresSearchSessionRepository
from taksitlio.search_sessions.status import SearchSessionStatus


class SearchSessionStatePersister:
    """Mirrors in-memory orchestrator mutations into V021 tables after each request."""

    def __init__(self, pg: PostgresSearchSessionRepository) -> None:
        self._pg = pg
        self._seen_events: set[str] = set()
        self._seen_jobs: set[str] = set()

    async def persist(self, orch: SearchOrchestrator, session_id: str) -> dict[str, Any]:
        session = orch.repo.get(session_id)
        if session is None:
            return {"persisted": False, "reason": "missing"}

        await self._upsert_session(session)
        version = orch.repo.get_version(session_id, session.active_query_version)
        if version is not None:
            await self._upsert_version(version, orch.parses.get(session_id))

        for event in orch.repo.events.get(session_id, []):
            if event.id in self._seen_events:
                continue
            await self._pg.append_event(
                session_id,
                query_version=event.query_version,
                event_type=event.event_type,
                display_message=event.display_message,
                data_origin=event.data_origin,
                payload=event.payload,
                severity=event.severity,
                event_id=event.id,
            )
            self._seen_events.add(event.id)

        for job_id, job in orch.llm_jobs.items():
            if getattr(job, "search_session_id", None) != session_id:
                continue
            payload = {
                "id": job.id,
                "search_session_id": job.search_session_id,
                "query_version": job.query_version,
                "conversation_state_version": job.conversation_state_version,
                "status": job.status.value if hasattr(job.status, "value") else job.status,
                "platform_role": "UNDERSTANDING_SERVICE",
                "input_payload": job.input_payload,
                "output_payload": job.output_payload,
                "error_code": job.error_code,
                "queued_at": job.queued_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }
            await self._pg.upsert_llm_job(payload)
            self._seen_jobs.add(job_id)

        for snap in orch.repo.partial_snapshots.get(session_id, []):
            await self._pg.upsert_partial_snapshot(
                session_id=session_id,
                query_version=int(snap.get("query_version") or session.active_query_version),
                label=str(snap.get("label") or "Ön sonuçlar"),
                product_ids=[p.get("product_id") for p in (snap.get("products") or [])],
                ranking_payload=snap,
            )

        for clar in orch.repo.clarifications.get(session_id, []):
            await self._pg.upsert_clarification_request(
                clarification_id=str(clar.get("clarification_id") or uuid.uuid4()),
                session_id=session_id,
                query_version=int(clar.get("query_version") or session.active_query_version),
                field=str(clar.get("field") or "unknown"),
                question_text=str(clar.get("question_text") or ""),
                question_signature=str(clar.get("question_signature") or ""),
                options=list(clar.get("options") or []),
                status="PENDING",
            )

        state = orch.states.get(session_id)
        if state is not None:
            await self._pg.upsert_session_metadata(
                session_id,
                {
                    "need_state": state.to_dict(),
                    "logos": {
                        k: [
                            {
                                "entity_id": i.entity_id,
                                "display_name": i.display_name,
                                "logo_cdn_url": i.logo_cdn_url,
                                "kind": i.kind,
                            }
                            for i in items
                        ]
                        for k, items in (orch.logo_rails.get(session_id) or {}).items()
                    },
                },
            )

        for metric in list(orch.repo.metrics.get(session_id, [])):
            await self._pg.record_metric(
                session_id,
                str(metric["metric_name"]),
                float(metric["metric_value"]),
                labels=dict(metric.get("labels") or {}),
            )
        orch.repo.metrics[session_id] = []

        return {"persisted": True, "session_id": session_id, "status": session.status.value}

    async def _upsert_session(self, session: Any) -> None:
        async with self._pg._pool.acquire() as conn:  # noqa: SLF001
            await conn.execute(
                """
                INSERT INTO search_sessions (
                    id, conversation_id, user_id, organization_id, status,
                    active_query_version, clarification_count, client_query_id,
                    started_at, completed_at, cancelled_at, metadata
                ) VALUES (
                    $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8::uuid,
                    $9, $10, $11, '{}'::jsonb
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    active_query_version = EXCLUDED.active_query_version,
                    clarification_count = EXCLUDED.clarification_count,
                    completed_at = EXCLUDED.completed_at,
                    cancelled_at = EXCLUDED.cancelled_at,
                    updated_at = NOW()
                """,
                session.id,
                session.conversation_id,
                session.user_id,
                session.organization_id,
                session.status.value if isinstance(session.status, SearchSessionStatus) else session.status,
                session.active_query_version,
                session.clarification_count,
                session.client_query_id,
                session.started_at,
                session.completed_at,
                session.cancelled_at,
            )

    async def _upsert_version(self, version: Any, parse: Any) -> None:
        snapshot = version.state_snapshot or (parse.to_dict() if parse is not None and hasattr(parse, "to_dict") else {})
        conf = version.confidence
        if conf is None and parse is not None:
            conf = getattr(parse, "confidence", None)
        requires = bool(version.requires_llm or getattr(parse, "requires_llm", False))
        async with self._pg._pool.acquire() as conn:  # noqa: SLF001
            await conn.execute(
                """
                INSERT INTO search_query_versions (
                    id, search_session_id, version_number, raw_user_text, normalized_text,
                    state_snapshot, confidence, requires_llm
                ) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb,$7,$8)
                ON CONFLICT (search_session_id, version_number) DO UPDATE SET
                    state_snapshot = EXCLUDED.state_snapshot,
                    confidence = EXCLUDED.confidence,
                    requires_llm = EXCLUDED.requires_llm
                """,
                version.id,
                version.search_session_id,
                version.version_number,
                version.raw_user_text,
                version.normalized_text,
                snapshot if isinstance(snapshot, dict) else {},
                conf,
                requires,
            )
