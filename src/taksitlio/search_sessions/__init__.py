"""Search sessions package (ADR-011)."""

from taksitlio.search_sessions.chat_bridge import (
    SearchChatBridgeResult,
    bridge_clarification_answer,
    bridge_search_start,
)
from taksitlio.search_sessions.orchestrator import SearchOrchestrator, build_demo_orchestrator
from taksitlio.search_sessions.postgres import PostgresSearchSessionRepository
from taksitlio.search_sessions.repository import (
    InMemorySearchSessionRepository,
    QueryVersion,
    SearchSession,
    SearchTimeoutPolicy,
    SessionEvent,
)
from taksitlio.search_sessions.status import (
    InvalidTransitionError,
    SearchSessionStatus,
    can_transition,
    transition,
)

__all__ = [
    "InMemorySearchSessionRepository",
    "InvalidTransitionError",
    "PostgresSearchSessionRepository",
    "QueryVersion",
    "SearchChatBridgeResult",
    "SearchOrchestrator",
    "SearchSession",
    "SearchSessionStatus",
    "SearchTimeoutPolicy",
    "SessionEvent",
    "bridge_clarification_answer",
    "bridge_search_start",
    "build_demo_orchestrator",
    "can_transition",
    "transition",
]
