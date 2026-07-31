"""DB / in-memory loaders for ADR-012 precedence + circuit breaker + feedback (V023)."""

from __future__ import annotations

import json
import uuid
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
    """Loads `source_precedence_policies` rows (is_active) matching V023."""

    pool: Any

    def load(self, policy_code: str = "DEFAULT") -> SourcePrecedencePolicy:
        return InMemoryPrecedencePolicyLoader().load(policy_code)

    async def load_async(self, policy_code: str = "DEFAULT") -> SourcePrecedencePolicy:
        async with self.pool.acquire() as conn:
            ver_row = await conn.fetchrow(
                """
                SELECT MAX(version) AS version
                FROM source_precedence_policies
                WHERE policy_code = $1 AND is_active = TRUE
                """,
                policy_code,
            )
            version = int(ver_row["version"]) if ver_row and ver_row["version"] else 1
            rows = await conn.fetch(
                """
                SELECT data_kind, source_order, version
                FROM source_precedence_policies
                WHERE policy_code = $1 AND version = $2 AND is_active = TRUE
                """,
                policy_code,
                version,
            )
        by_kind: dict[str, tuple[str, ...]] = {}
        for row in rows:
            order = row["source_order"] or []
            by_kind[str(row["data_kind"])] = tuple(str(x) for x in order)
        if not by_kind:
            return InMemoryPrecedencePolicyLoader().load(policy_code)
        return SourcePrecedencePolicy(
            policy_code=policy_code,
            version=version,
            precedence_by_kind=by_kind,
        )


@dataclass
class PostgresCircuitBreakerStore:
    """Maps V023 quality_circuit_breakers (scope/source_key/action/is_open)."""

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
        async with self.pool.acquire() as conn:
            for action in actions:
                await conn.execute(
                    """
                    INSERT INTO quality_circuit_breakers
                        (scope, source_key, action, is_open)
                    VALUES ($1, $2, $3, TRUE)
                    ON CONFLICT (scope, source_key, action) DO UPDATE SET
                        is_open = TRUE,
                        opened_at = NOW(),
                        closed_at = NULL
                    """,
                    "MERCHANT_PRICE",
                    source_id,
                    action.value,
                )

    async def hydrate(self, source_id: Optional[str] = None) -> QualityCircuitBreaker:
        async with self.pool.acquire() as conn:
            if source_id:
                rows = await conn.fetch(
                    """
                    SELECT scope, source_key, action, metric_value, threshold_value
                    FROM quality_circuit_breakers
                    WHERE is_open = TRUE AND source_key = $1
                    """,
                    source_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT scope, source_key, action, metric_value, threshold_value
                    FROM quality_circuit_breakers
                    WHERE is_open = TRUE
                    """
                )
        key = source_id or "_global"
        cb = self.get(key)
        for row in rows:
            try:
                action = BreakerAction(row["action"])
            except ValueError:
                continue
            if action is BreakerAction.NONE:
                continue
            cb.disabled.add(action)
            scope = str(row["scope"] or "")
            metric = row["metric_value"]
            threshold = row["threshold_value"]
            if scope == "MERCHANT_PRICE" and metric is not None:
                cb.broken_price_rate = float(metric)
                if threshold is not None:
                    cb.price_threshold = float(threshold)
            elif scope == "BANK_CAMPAIGN" and metric is not None:
                cb.campaign_mismatch_count = int(float(metric))
            elif scope == "IMAGE_SOURCE" and metric is not None:
                cb.broken_image_rate = float(metric)
                if threshold is not None:
                    cb.image_threshold = float(threshold)
        return cb


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


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

    async def save_feedback_async(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.save_feedback(row)
        return row

    async def save_shadow_async(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.save_shadow(row)
        return row

    async def save_error_class_async(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.save_error_class(row)
        return row

    async def metrics_by_error_class_async(self) -> dict[str, int]:
        return self.metrics_by_error_class()


@dataclass
class PostgresFeedbackStore(InMemoryFeedbackStore):
    """Persists feedback / shadow / error-class into V023 tables (production).

    Sync ``save_*`` methods still update the in-process cache for dual-read
    during the request; durable writes go through ``*_async``.
    """

    pool: Any = None

    def save_feedback(self, payload: Mapping[str, Any]) -> None:
        # Prefer async path in production HTTP handlers.
        super().save_feedback(payload)

    async def save_feedback_async(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        fid = str(row.get("feedback_id") or uuid.uuid4())
        row["feedback_id"] = fid
        self.save_feedback(row)
        if self.pool is None:
            return row
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO feedback_result_snapshots (
                    feedback_id, query_version, parsed_constraints, catalog_revision,
                    price_snapshot, campaign_snapshot, selected_product, selected_bank,
                    response_fact_ids, error_class, user_note
                ) VALUES (
                    $1::uuid, $2, $3::jsonb, $4, $5, $6, $7, $8, $9::text[], $10, $11
                )
                """,
                fid,
                int(row.get("query_version") or 0),
                _json(row.get("parsed_constraints") or {}),
                row.get("catalog_revision"),
                row.get("price_snapshot"),
                row.get("campaign_snapshot"),
                row.get("selected_product"),
                row.get("selected_bank"),
                list(row.get("response_fact_ids") or []),
                row.get("error_class"),
                row.get("user_note"),
            )
        return row

    async def save_shadow_async(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.save_shadow(row)
        if self.pool is None:
            return row
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO shadow_mode_comparisons (
                    comparison_key, live_payload, shadow_payload, diffs, shown_to_user
                ) VALUES ($1, $2::jsonb, $3::jsonb, $4::text[], $5)
                """,
                str(row.get("comparison_key") or row.get("key") or "default"),
                _json(row.get("live") or row.get("live_payload") or {}),
                _json(row.get("shadow") or row.get("shadow_payload") or {}),
                list(row.get("diffs") or []),
                bool(row.get("shown_to_user", False)),
            )
        return row

    async def save_error_class_async(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.save_error_class(row)
        if self.pool is None:
            return row
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO error_class_events (
                    error_class, source_component, metric_key, payload
                ) VALUES ($1, $2, $3, $4::jsonb)
                """,
                str(row.get("error_class")),
                row.get("owner") or row.get("source_component"),
                row.get("metric_key"),
                _json(row),
            )
        return row

    async def metrics_by_error_class_async(self) -> dict[str, int]:
        if self.pool is None:
            return self.metrics_by_error_class()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT error_class, COUNT(*)::bigint AS n
                FROM error_class_events
                GROUP BY error_class
                """
            )
        return {str(r["error_class"]): int(r["n"]) for r in rows}


__all__ = [
    "CircuitBreakerStore",
    "InMemoryCircuitBreakerStore",
    "InMemoryFeedbackStore",
    "InMemoryPrecedencePolicyLoader",
    "PostgresCircuitBreakerStore",
    "PostgresFeedbackStore",
    "PostgresPrecedencePolicyLoader",
    "PrecedencePolicyLoader",
]
