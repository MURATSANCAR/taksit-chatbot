"""Search sessions package (ADR-011)."""

from taksitlio.search_sessions.orchestrator import SearchOrchestrator, build_demo_orchestrator
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
    "QueryVersion",
    "SearchOrchestrator",
    "SearchSession",
    "SearchSessionStatus",
    "SearchTimeoutPolicy",
    "SessionEvent",
    "build_demo_orchestrator",
    "can_transition",
    "transition",
]
