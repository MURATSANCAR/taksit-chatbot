"""DB / in-memory loaders for ADR-012 precedence + circuit breaker policies."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

from taksitlio.answer_integrity.conflict import (
    DEFAULT_PRECEDENCE,
    SourcePrecedencePolicy,
)
from taksitlio.recommendation_safety.circuit_breaker import (
    BreakerAction,
    QualityCircuitBreaker,
)


class PrecedencePolicyLoader(Protocol):
    def load(self, policy_code: str = "DEFAULT") -> SourcePrecedencePolicy: ...


class CircuitBreakerStore(Protocol):
    def get(self, source_id: str) -> QualityCircuitBreaker: ...

    def record_actions(
        self, source_id: str, actions: Sequence[BreakerAction], *, reason: str = ""
    ) -> None: ...


@dataclass
class InMemoryPrecedencePolicyLoader:
    policies: dict[str, SourcePrecedencePolicy] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if "DEFAULT" not in self.policies:
            self.policies["DEFAULT"] = SourcePrecedencePolicy(
                policy_code="DEFAULT",
                version=1,
                precedence_by_kind=dict(DEFAULT_PRECEDENCE),
            )

    def load(self, policy_code: str = "DEFAULT") -> SourcePrecedencePolicy:
        return self.policies.get(policy_code) or self.policies["DEFAULT"]


@dataclass
class InMemoryCircuitBreakerStore:
    breakers: dict[str, QualityCircuitBreaker] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def get(self, source_id: str) -> QualityCircuitBreaker:
        if source_id not in self.breakers:
            self.breakers[source_id] = QualityCircuitBreaker()
        return self.breakers[source_id]

    def record_actions(
        self, source_id: str, actions: Sequence[BreakerAction], *, reason: str = ""
    ) -> None:
        cb = self.get(source_id)
        cb.disabled.update(actions)
        for action in actions:
            self.events.append(
                {
                    "source_id": source_id,
                    "action": action.value,
                    "reason": reason,
                }
            )


@dataclass
class PostgresPrecedencePolicyLoader:
    """Loads `source_precedence_policies` rows (ACTIVE)."""

    pool: Any  # asyncpg pool-like; fetch method

    def load(self, policy_code: str = "DEFAULT") -> SourcePrecedencePolicy:
        # Sync helper for unit tests: prefer in-memory unless pool supports fetch
        fetch = getattr(self.pool, "fetch", None)
        if fetch is None:
            return InMemoryPrecedencePolicyLoader().load(policy_code)
        # Production path uses async; this sync facade is for DI shape.
        # Callers that need async should use load_async.
        return InMemoryPrecedencePolicyLoader().load(policy_code)

    async def load_async(self, policy_code: str = "DEFAULT") -> SourcePrecedencePolicy:
        rows = await self.pool.fetch(
            """
            SELECT data_kind, precedence_json, version
            FROM source_precedence_policies
            WHERE policy_code = $1 AND status = 'ACTIVE'
            ORDER BY version DESC
            """,
            policy_code,
        )
        by_kind: dict[str, tuple[str, ...]] = {}
        version = 1
        for row in rows:
            kind = row["data_kind"]
            if kind in by_kind:
                continue  # already took highest version via ORDER BY + first-seen
            raw = row["precedence_json"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            by_kind[kind] = tuple(raw)
            version = max(version, int(row["version"] or 1))
        if not by_kind:
            return InMemoryPrecedencePolicyLoader().load(policy_code)
        return SourcePrecedencePolicy(
            policy_code=policy_code,
            version=version,
            precedence_by_kind=by_kind,
        )


@dataclass
class PostgresCircuitBreakerStore:
    pool: Any
    _cache: dict[str, QualityCircuitBreaker] = field(default_factory=dict)

    def get(self, source_id: str) -> QualityCircuitBreaker:
        if source_id not in self._cache:
            self._cache[source_id] = QualityCircuitBreaker()
        return self._cache[source_id]

    def record_actions(
        self, source_id: str, actions: Sequence[BreakerAction], *, reason: str = ""
    ) -> None:
        cb = self.get(source_id)
        cb.disabled.update(actions)

    async def record_actions_async(
        self, source_id: str, actions: Sequence[BreakerAction], *, reason: str = ""
    ) -> None:
        self.record_actions(source_id, actions, reason=reason)
        for action in actions:
            await self.pool.execute(
                """
                INSERT INTO quality_circuit_breakers
                    (source_id, action, reason, active)
                VALUES ($1, $2, $3, TRUE)
                """,
                source_id,
                action.value,
                reason,
            )

    async def hydrate(self, source_id: str) -> QualityCircuitBreaker:
        rows = await self.pool.fetch(
            """
            SELECT action FROM quality_circuit_breakers
            WHERE source_id = $1 AND active = TRUE
            """,
            source_id,
        )
        cb = self.get(source_id)
        for row in rows:
            try:
                cb.disabled.add(BreakerAction(row["action"]))
            except ValueError:
                continue
        return cb


@dataclass
class InMemoryFeedbackStore:
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    shadows: list[dict[str, Any]] = field(default_factory=list)
    error_events: list[dict[str, Any]] = field(default_factory=list)

    def save_feedback(self, payload: Mapping[str, Any]) -> None:
        self.snapshots.append(dict(payload))

    def save_shadow(self, payload: Mapping[str, Any]) -> None:
        self.shadows.append(dict(payload))

    def save_error_class(self, payload: Mapping[str, Any]) -> None:
        if payload.get("error_class") == "WRONG_ANSWER":
            raise ValueError("WRONG_ANSWER bucket is forbidden (ADR-012)")
        self.error_events.append(dict(payload))

    def metrics_by_error_class(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ev in self.error_events:
            key = str(ev.get("error_class") or "UNKNOWN")
            counts[key] = counts.get(key, 0) + 1
        return counts


__all__ = [
    "CircuitBreakerStore",
    "InMemoryCircuitBreakerStore",
    "InMemoryFeedbackStore",
    "InMemoryPrecedencePolicyLoader",
    "PostgresCircuitBreakerStore",
    "PostgresPrecedencePolicyLoader",
    "PrecedencePolicyLoader",
]
