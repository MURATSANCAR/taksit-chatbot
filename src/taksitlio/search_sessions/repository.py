"""Search session domain models and in-memory repository (ADR-011)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from taksitlio.search_sessions.status import (
    SearchSessionStatus,
    transition,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SearchTimeoutPolicy:
    policy_code: str = "SEARCH_DEFAULT"
    queue_soft_deadline_ms: int = 2000
    inference_soft_deadline_ms: int = 8000
    partial_result_deadline_ms: int = 4000
    ux_fallback_deadline_ms: int = 12000
    hard_timeout_ms: int = 32000
    max_clarifications_per_session: int = 2
    max_clarifications_per_message: int = 1


@dataclass
class SearchSession:
    id: str
    conversation_id: str
    status: SearchSessionStatus
    active_query_version: int = 1
    clarification_count: int = 0
    user_id: Optional[str] = None
    organization_id: Optional[str] = None
    client_query_id: Optional[str] = None
    started_at: datetime = field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    superseded_by: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryVersion:
    id: str
    search_session_id: str
    version_number: int
    raw_user_text: str
    normalized_text: str
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    confidence: Optional[float] = None
    requires_llm: bool = False
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class SessionEvent:
    id: str
    search_session_id: str
    query_version: int
    event_type: str
    severity: str = "INFO"
    display_message: Optional[str] = None
    data_origin: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)


class InMemorySearchSessionRepository:
    """Process-local store for tests and in-memory demo."""

    def __init__(self) -> None:
        self.sessions: dict[str, SearchSession] = {}
        self.versions: dict[str, list[QueryVersion]] = {}
        self.events: dict[str, list[SessionEvent]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.clarifications: dict[str, list[dict[str, Any]]] = {}
        self.llm_jobs: dict[str, dict[str, Any]] = {}
        self.partial_snapshots: dict[str, list[dict[str, Any]]] = {}
        self.result_snapshots: dict[str, list[dict[str, Any]]] = {}
        self.metrics: dict[str, list[dict[str, Any]]] = {}
        self.clarification_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.policy = SearchTimeoutPolicy()

    def create_session(
        self,
        *,
        conversation_id: str,
        message: str,
        client_query_id: Optional[str] = None,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> tuple[SearchSession, QueryVersion]:
        sid = str(uuid.uuid4())
        session = SearchSession(
            id=sid,
            conversation_id=conversation_id,
            status=SearchSessionStatus.RECEIVED,
            user_id=user_id,
            organization_id=organization_id,
            client_query_id=client_query_id,
        )
        self.sessions[sid] = session
        version = self.append_query_version(sid, raw_user_text=message)
        self.append_message(sid, version.version_number, role="USER", content=message)
        return session, version

    def get(self, session_id: str) -> Optional[SearchSession]:
        return self.sessions.get(session_id)

    def set_status(self, session_id: str, target: SearchSessionStatus) -> SearchSession:
        session = self.sessions[session_id]
        session.status = transition(session.status, target)
        if target in {
            SearchSessionStatus.COMPLETED,
            SearchSessionStatus.COMPLETED_DEGRADED,
            SearchSessionStatus.FAILED,
        }:
            session.completed_at = _utcnow()
        if target == SearchSessionStatus.CANCELLED:
            session.cancelled_at = _utcnow()
        return session

    def append_query_version(self, session_id: str, *, raw_user_text: str) -> QueryVersion:
        session = self.sessions[session_id]
        existing = self.versions.setdefault(session_id, [])
        next_n = (existing[-1].version_number + 1) if existing else 1
        version = QueryVersion(
            id=str(uuid.uuid4()),
            search_session_id=session_id,
            version_number=next_n,
            raw_user_text=raw_user_text,
            normalized_text=raw_user_text.strip().lower(),
        )
        existing.append(version)
        session.active_query_version = next_n
        return version

    def get_version(self, session_id: str, version_number: int) -> Optional[QueryVersion]:
        for v in self.versions.get(session_id, []):
            if v.version_number == version_number:
                return v
        return None

    def append_message(
        self,
        session_id: str,
        query_version: int,
        *,
        role: str,
        content: str,
    ) -> None:
        self.messages.setdefault(session_id, []).append(
            {
                "query_version": query_version,
                "role": role,
                "content": content,
                "created_at": _utcnow().isoformat(),
            }
        )

    def append_event(
        self,
        session_id: str,
        *,
        query_version: int,
        event_type: str,
        display_message: Optional[str] = None,
        data_origin: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        severity: str = "INFO",
    ) -> SessionEvent:
        event = SessionEvent(
            id=str(uuid.uuid4()),
            search_session_id=session_id,
            query_version=query_version,
            event_type=event_type,
            severity=severity,
            display_message=display_message,
            data_origin=data_origin,
            payload=dict(payload or {}),
        )
        self.events.setdefault(session_id, []).append(event)
        return event

    def list_events(self, session_id: str, *, after_id: Optional[str] = None) -> list[SessionEvent]:
        events = self.events.get(session_id, [])
        if after_id is None:
            return list(events)
        seen = False
        out: list[SessionEvent] = []
        for e in events:
            if seen:
                out.append(e)
            elif e.id == after_id:
                seen = True
        return out

    def record_metric(self, session_id: str, name: str, value: float, **labels: Any) -> None:
        self.metrics.setdefault(session_id, []).append(
            {
                "metric_name": name,
                "metric_value": value,
                "labels": labels,
                "recorded_at": _utcnow().isoformat(),
            }
        )
