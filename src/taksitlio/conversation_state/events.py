"""Event sink + metrics hooks (no PII / no session_id labels)."""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import uuid4
from datetime import datetime, timezone

from taksitlio.conversation_state.domain import ConversationStateChangedEvent

logger = logging.getLogger("taksitlio.conversation_state")


class ConversationStateEventSink(Protocol):
    async def publish(self, event: ConversationStateChangedEvent) -> None: ...


class NoOpConversationStateEventSink:
    async def publish(self, event: ConversationStateChangedEvent) -> None:
        return None


class InMemoryConversationStateEventSink:
    def __init__(self) -> None:
        self.events: list[ConversationStateChangedEvent] = []

    async def publish(self, event: ConversationStateChangedEvent) -> None:
        self.events.append(event)


class MetricsHook(Protocol):
    def incr(self, name: str, *, value: float = 1.0) -> None: ...

    def observe(self, name: str, value: float) -> None: ...


class NoOpMetricsHook:
    def incr(self, name: str, *, value: float = 1.0) -> None:
        return None

    def observe(self, name: str, value: float) -> None:
        return None


class InMemoryMetricsHook:
    def __init__(self) -> None:
        self.counters: dict[str, float] = {}
        self.observations: dict[str, list[float]] = {}

    def incr(self, name: str, *, value: float = 1.0) -> None:
        # Never accept session_id-like labels — names only
        if "session" in name.lower() and name.endswith("_id"):
            return
        self.counters[name] = self.counters.get(name, 0.0) + value

    def observe(self, name: str, value: float) -> None:
        self.observations.setdefault(name, []).append(value)


def make_state_changed_event(
    *,
    session_id,
    previous_revision: int,
    new_revision: int,
    event_type: str,
    operation_types: list[str],
    correlation_id: str | None,
) -> ConversationStateChangedEvent:
    return ConversationStateChangedEvent(
        event_id=str(uuid4()),
        session_id=session_id,
        previous_revision=previous_revision,
        new_revision=new_revision,
        event_type=event_type,
        operation_types=tuple(operation_types),
        correlation_id=correlation_id,
        occurred_at=datetime.now(timezone.utc),
    )


def safe_log_update(
    *,
    correlation_id: str | None,
    revision: int | None,
    operation_count: int,
    decision: str,
    reason_code: str | None,
    duration_ms: float,
    serialized_size_bytes: int,
) -> None:
    logger.info(
        "conversation_state_update correlation_id=%s revision=%s ops=%s decision=%s "
        "reason=%s duration_ms=%.2f size_bytes=%s",
        correlation_id,
        revision,
        operation_count,
        decision,
        reason_code,
        duration_ms,
        serialized_size_bytes,
    )
