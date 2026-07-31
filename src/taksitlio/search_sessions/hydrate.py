"""Hydrate SearchOrchestrator runtime from Postgres snapshot (ADR-011 restart reload)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from taksitlio.llm_routing import LlmJobStatus, LlmUnderstandingJob
from taksitlio.query_state import QueryNeedState
from taksitlio.search_sessions.orchestrator import LogoCandidate, SearchOrchestrator
from taksitlio.search_sessions.repository import QueryVersion, SearchSession, SessionEvent


@dataclass
class SessionSnapshot:
    session: SearchSession
    versions: list[QueryVersion] = field(default_factory=list)
    events: list[SessionEvent] = field(default_factory=list)
    clarifications: list[dict[str, Any]] = field(default_factory=list)
    partials: list[dict[str, Any]] = field(default_factory=list)
    llm_jobs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value


def hydrate_orchestrator(orch: SearchOrchestrator, snapshot: SessionSnapshot) -> SearchSession:
    """Install a Postgres-loaded session into the in-memory orchestrator hot path."""

    session = snapshot.session
    sid = session.id
    orch.repo.sessions[sid] = session
    orch.repo.versions[sid] = list(snapshot.versions)
    orch.repo.events[sid] = list(snapshot.events)
    orch.repo.clarifications[sid] = list(snapshot.clarifications)
    orch.repo.partial_snapshots[sid] = list(snapshot.partials)

    meta = dict(snapshot.metadata or session.metadata or {})
    need = meta.get("need_state")
    if isinstance(need, Mapping):
        orch.states[sid] = QueryNeedState.from_dict(dict(need))
    elif sid not in orch.states:
        orch.states[sid] = QueryNeedState()

    logos = meta.get("logos") or {}
    if isinstance(logos, Mapping):
        rails: dict[str, list[LogoCandidate]] = {}
        for kind, items in logos.items():
            if not isinstance(items, list):
                continue
            rails[str(kind)] = [
                LogoCandidate(
                    entity_id=str(i.get("entity_id") or ""),
                    display_name=str(i.get("display_name") or ""),
                    logo_cdn_url=i.get("logo_cdn_url"),
                    kind=str(i.get("kind") or kind),
                )
                for i in items
                if isinstance(i, Mapping)
            ]
        if rails:
            orch.logo_rails[sid] = rails

    active = next(
        (v for v in snapshot.versions if v.version_number == session.active_query_version),
        snapshot.versions[-1] if snapshot.versions else None,
    )
    if active is not None and active.state_snapshot:
        orch.parses[sid] = dict(active.state_snapshot)

    if snapshot.clarifications:
        pending = next(
            (c for c in snapshot.clarifications if c.get("status") == "PENDING"),
            snapshot.clarifications[-1],
        )
        orch.clarifications[sid] = dict(pending)

    for raw in snapshot.llm_jobs:
        job_id = str(raw.get("id") or "")
        if not job_id:
            continue
        status_raw = raw.get("status") or "QUEUED"
        try:
            status = LlmJobStatus(str(status_raw))
        except ValueError:
            status = LlmJobStatus.QUEUED
        orch.llm_jobs[job_id] = LlmUnderstandingJob(
            id=job_id,
            search_session_id=str(raw.get("search_session_id") or sid),
            query_version=int(raw.get("query_version") or session.active_query_version),
            conversation_state_version=int(raw.get("conversation_state_version") or 0),
            status=status,
            input_payload=dict(_parse_jsonish(raw.get("input_payload")) or {}),
            output_payload=(
                dict(_parse_jsonish(raw.get("output_payload")))
                if raw.get("output_payload") is not None
                else None
            ),
            error_code=raw.get("error_code"),
            queued_at=raw.get("queued_at") or datetime.now(timezone.utc),
            started_at=raw.get("started_at"),
            completed_at=raw.get("completed_at"),
        )

    return session


async def ensure_session_loaded(
    orch: SearchOrchestrator,
    session_id: str,
    *,
    pg: Any = None,
) -> Optional[SearchSession]:
    """Return session from memory, or hydrate from Postgres on miss."""

    existing = orch.repo.get(session_id)
    if existing is not None:
        return existing
    if pg is None or not hasattr(pg, "load_full_session"):
        return None
    snapshot = await pg.load_full_session(session_id)
    if snapshot is None:
        return None
    return hydrate_orchestrator(orch, snapshot)


__all__ = [
    "SessionSnapshot",
    "ensure_session_loaded",
    "hydrate_orchestrator",
]
