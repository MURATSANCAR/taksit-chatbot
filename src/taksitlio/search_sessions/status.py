"""ADR-011 search session status machine."""

from __future__ import annotations

from enum import Enum


class SearchSessionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    FAST_PARSING = "FAST_PARSING"
    ENTITY_RESOLVING = "ENTITY_RESOLVING"
    GAP_ANALYSIS = "GAP_ANALYSIS"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    WAITING_USER_ANSWER = "WAITING_USER_ANSWER"
    FAST_RETRIEVAL = "FAST_RETRIEVAL"
    LLM_QUEUED = "LLM_QUEUED"
    LLM_RUNNING = "LLM_RUNNING"
    PARTIAL_RESULTS_READY = "PARTIAL_RESULTS_READY"
    FINANCE_OPTIONS_LOADING = "FINANCE_OPTIONS_LOADING"
    RANKING = "RANKING"
    COMPLETED = "COMPLETED"
    COMPLETED_DEGRADED = "COMPLETED_DEGRADED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


# Allowed transitions (from → frozenset of to)
_TRANSITIONS: dict[SearchSessionStatus, frozenset[SearchSessionStatus]] = {
    SearchSessionStatus.RECEIVED: frozenset({SearchSessionStatus.FAST_PARSING}),
    SearchSessionStatus.FAST_PARSING: frozenset(
        {
            SearchSessionStatus.ENTITY_RESOLVING,
            SearchSessionStatus.GAP_ANALYSIS,
            SearchSessionStatus.FAST_RETRIEVAL,
            SearchSessionStatus.CLARIFICATION_REQUIRED,
            SearchSessionStatus.LLM_QUEUED,
            SearchSessionStatus.FAILED,
            SearchSessionStatus.CANCELLED,
        }
    ),
    SearchSessionStatus.ENTITY_RESOLVING: frozenset(
        {
            SearchSessionStatus.GAP_ANALYSIS,
            SearchSessionStatus.FAST_RETRIEVAL,
            SearchSessionStatus.FAILED,
            SearchSessionStatus.CANCELLED,
        }
    ),
    SearchSessionStatus.GAP_ANALYSIS: frozenset(
        {
            SearchSessionStatus.FAST_RETRIEVAL,
            SearchSessionStatus.CLARIFICATION_REQUIRED,
            SearchSessionStatus.LLM_QUEUED,
            SearchSessionStatus.FAILED,
            SearchSessionStatus.CANCELLED,
        }
    ),
    SearchSessionStatus.CLARIFICATION_REQUIRED: frozenset(
        {
            SearchSessionStatus.WAITING_USER_ANSWER,
            SearchSessionStatus.CANCELLED,
            SearchSessionStatus.SUPERSEDED,
        }
    ),
    SearchSessionStatus.WAITING_USER_ANSWER: frozenset(
        {
            SearchSessionStatus.FAST_PARSING,
            SearchSessionStatus.FAST_RETRIEVAL,
            SearchSessionStatus.CLARIFICATION_REQUIRED,
            SearchSessionStatus.LLM_QUEUED,
            SearchSessionStatus.CANCELLED,
            SearchSessionStatus.SUPERSEDED,
        }
    ),
    SearchSessionStatus.FAST_RETRIEVAL: frozenset(
        {
            SearchSessionStatus.RANKING,
            SearchSessionStatus.FINANCE_OPTIONS_LOADING,
            SearchSessionStatus.FAST_PARSING,
            SearchSessionStatus.FAILED,
            SearchSessionStatus.CANCELLED,
        }
    ),
    SearchSessionStatus.LLM_QUEUED: frozenset(
        {
            SearchSessionStatus.LLM_RUNNING,
            SearchSessionStatus.FAST_PARSING,
            SearchSessionStatus.CANCELLED,
            SearchSessionStatus.SUPERSEDED,
            SearchSessionStatus.TIMED_OUT,
        }
    ),
    SearchSessionStatus.LLM_RUNNING: frozenset(
        {
            SearchSessionStatus.PARTIAL_RESULTS_READY,
            SearchSessionStatus.RANKING,
            SearchSessionStatus.FAST_PARSING,
            SearchSessionStatus.TIMED_OUT,
            SearchSessionStatus.FAILED,
            SearchSessionStatus.CANCELLED,
            SearchSessionStatus.SUPERSEDED,
            SearchSessionStatus.COMPLETED_DEGRADED,
        }
    ),
    SearchSessionStatus.PARTIAL_RESULTS_READY: frozenset(
        {
            SearchSessionStatus.FINANCE_OPTIONS_LOADING,
            SearchSessionStatus.RANKING,
            SearchSessionStatus.FAST_PARSING,
            SearchSessionStatus.COMPLETED,
            SearchSessionStatus.COMPLETED_DEGRADED,
            SearchSessionStatus.CANCELLED,
            SearchSessionStatus.SUPERSEDED,
            SearchSessionStatus.TIMED_OUT,
        }
    ),
    SearchSessionStatus.FINANCE_OPTIONS_LOADING: frozenset(
        {
            SearchSessionStatus.RANKING,
            SearchSessionStatus.PARTIAL_RESULTS_READY,
            SearchSessionStatus.FAST_PARSING,
            SearchSessionStatus.CANCELLED,
            SearchSessionStatus.TIMED_OUT,
        }
    ),
    SearchSessionStatus.RANKING: frozenset(
        {
            SearchSessionStatus.COMPLETED,
            SearchSessionStatus.COMPLETED_DEGRADED,
            SearchSessionStatus.FAST_PARSING,
            SearchSessionStatus.FAILED,
            SearchSessionStatus.CANCELLED,
        }
    ),
    SearchSessionStatus.TIMED_OUT: frozenset(
        {SearchSessionStatus.COMPLETED_DEGRADED, SearchSessionStatus.RANKING, SearchSessionStatus.CANCELLED}
    ),
    # Follow-up refinement reopen (same search session).
    SearchSessionStatus.COMPLETED: frozenset({SearchSessionStatus.FAST_PARSING}),
    SearchSessionStatus.COMPLETED_DEGRADED: frozenset({SearchSessionStatus.FAST_PARSING}),
    SearchSessionStatus.FAILED: frozenset(),
    SearchSessionStatus.CANCELLED: frozenset(),
    SearchSessionStatus.SUPERSEDED: frozenset(),
}

# Hard terminals: follow-up must start a new search session.
HARD_TERMINAL_STATUSES: frozenset[SearchSessionStatus] = frozenset(
    {
        SearchSessionStatus.CANCELLED,
        SearchSessionStatus.FAILED,
        SearchSessionStatus.TIMED_OUT,
        SearchSessionStatus.SUPERSEDED,
    }
)


class InvalidTransitionError(ValueError):
    pass


def can_transition(current: SearchSessionStatus, target: SearchSessionStatus) -> bool:
    if current == target:
        return True
    return target in _TRANSITIONS.get(current, frozenset())


def is_hard_terminal(status: SearchSessionStatus) -> bool:
    return status in HARD_TERMINAL_STATUSES


def transition(current: SearchSessionStatus, target: SearchSessionStatus) -> SearchSessionStatus:
    if not can_transition(current, target):
        raise InvalidTransitionError(f"Cannot transition {current.value} → {target.value}")
    return target
