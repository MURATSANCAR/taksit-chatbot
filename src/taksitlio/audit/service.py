"""Configuration audit trail for admin mutations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class AuditRecord:
    actor_id: str | None
    entity_type: str
    entity_id: str
    action: str
    before_value: dict[str, Any] | None
    after_value: dict[str, Any] | None
    reason: str | None = None
    correlation_id: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AuditStore(Protocol):
    async def append(self, record: AuditRecord) -> None: ...

    async def list_for_entity(
        self, entity_type: str, entity_id: str
    ) -> list[AuditRecord]: ...


class InMemoryAuditStore:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def append(self, record: AuditRecord) -> None:
        self.records.append(record)

    async def list_for_entity(
        self, entity_type: str, entity_id: str
    ) -> list[AuditRecord]:
        return [
            r
            for r in self.records
            if r.entity_type == entity_type and r.entity_id == entity_id
        ]


class AuditService:
    """Records before/after JSON for configuration changes."""

    def __init__(self, store: AuditStore) -> None:
        self._store = store

    async def record(
        self,
        *,
        actor_id: str | None,
        entity_type: str,
        entity_id: str,
        action: str,
        before_value: dict[str, Any] | None,
        after_value: dict[str, Any] | None,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditRecord:
        record = AuditRecord(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_value=_jsonable(before_value),
            after_value=_jsonable(after_value),
            reason=reason,
            correlation_id=correlation_id or str(uuid4()),
        )
        await self._store.append(record)
        return record

    async def model_activated(
        self,
        *,
        actor_id: str | None,
        profile_code: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None = None,
    ) -> AuditRecord:
        return await self.record(
            actor_id=actor_id,
            entity_type="ai_model_profiles",
            entity_id=profile_code,
            action="ACTIVATE",
            before_value=before,
            after_value=after,
            reason=reason,
        )

    async def route_changed(
        self,
        *,
        actor_id: str | None,
        task_code: str,
        route_version: int,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None = None,
    ) -> AuditRecord:
        return await self.record(
            actor_id=actor_id,
            entity_type="ai_route_versions",
            entity_id=f"{task_code}:v{route_version}",
            action="UPDATE",
            before_value=before,
            after_value=after,
            reason=reason,
        )

    async def prompt_activated(
        self,
        *,
        actor_id: str | None,
        prompt_code: str,
        version: int,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None = None,
    ) -> AuditRecord:
        return await self.record(
            actor_id=actor_id,
            entity_type="ai_prompt_versions",
            entity_id=f"{prompt_code}:v{version}",
            action="ACTIVATE",
            before_value=before,
            after_value=after,
            reason=reason,
        )

    async def policy_changed(
        self,
        *,
        actor_id: str | None,
        entity_type: str,
        policy_code: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None = None,
    ) -> AuditRecord:
        return await self.record(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=policy_code,
            action="UPDATE",
            before_value=before,
            after_value=after,
            reason=reason,
        )


def _jsonable(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))
