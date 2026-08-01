"""Search sessions package (ADR-011)."""

from taksitlio.search_sessions.catalog_pool import refresh_orchestrator_from_catalog
from taksitlio.search_sessions.chat_bridge import (
    SearchChatBridgeResult,
    bridge_clarification_answer,
    bridge_search_start,
)
from taksitlio.search_sessions.metrics import GLOBAL_SEARCH_METRICS, MetricsRegistry
from taksitlio.search_sessions.orchestrator import (
    SearchOrchestrator,
    build_demo_orchestrator,
    build_empty_orchestrator,
)
from taksitlio.search_sessions.persist import SearchSessionStatePersister
from taksitlio.search_sessions.postgres import PostgresSearchSessionRepository
from taksitlio.search_sessions.repository import (
    InMemorySearchSessionRepository,
    QueryVersion,
    SearchSession,
    SearchTimeoutPolicy,
    SessionEvent,
)
from taksitlio.search_sessions.status import (
    HARD_TERMINAL_STATUSES,
    InvalidTransitionError,
    SearchSessionStatus,
    can_transition,
    is_hard_terminal,
    transition,
)

__all__ = [
    "GLOBAL_SEARCH_METRICS",
    "HARD_TERMINAL_STATUSES",
    "InMemorySearchSessionRepository",
    "InvalidTransitionError",
    "MetricsRegistry",
    "PostgresSearchSessionRepository",
    "QueryVersion",
    "SearchChatBridgeResult",
    "SearchOrchestrator",
    "SearchSession",
    "SearchSessionStatePersister",
    "SearchSessionStatus",
    "SearchTimeoutPolicy",
    "SessionEvent",
    "bridge_clarification_answer",
    "bridge_search_start",
    "build_demo_orchestrator",
    "build_empty_orchestrator",
    "can_transition",
    "is_hard_terminal",
    "refresh_orchestrator_from_catalog",
    "transition",
]
