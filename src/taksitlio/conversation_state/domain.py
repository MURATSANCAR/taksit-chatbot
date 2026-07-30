"""Conversation state domain models (platform lifecycle — not business categories)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import UUID, uuid4

SCHEMA_VERSION_V1 = "conversation-state.v1"
CURRENT_SCHEMA_VERSION = SCHEMA_VERSION_V1


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ActorType(str, Enum):
    ANONYMOUS = "ANONYMOUS"
    AUTHENTICATED = "AUTHENTICATED"


class CasStatus(str, Enum):
    APPLIED = "APPLIED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    INVALID_STATE = "INVALID_STATE"
    DUPLICATE_PAYLOAD_MISMATCH = "DUPLICATE_PAYLOAD_MISMATCH"


@dataclass(frozen=True)
class Actor:
    type: ActorType
    user_id: str | None = None
    device_session_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "user_id": self.user_id,
            "device_session_ref": self.device_session_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Actor":
        return cls(
            type=ActorType(str(data.get("type") or ActorType.ANONYMOUS.value)),
            user_id=data.get("user_id"),
            device_session_ref=data.get("device_session_ref"),
        )


@dataclass(frozen=True)
class ClarificationState:
    required: bool = False
    reason_code: str | None = None
    missing_concepts: tuple[str, ...] = ()
    question_intent: str | None = None
    asked_at_revision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "reason_code": self.reason_code,
            "missing_concepts": list(self.missing_concepts),
            "question_intent": self.question_intent,
            "asked_at_revision": self.asked_at_revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ClarificationState":
        if not data:
            return cls()
        concepts = data.get("missing_concepts") or []
        return cls(
            required=bool(data.get("required")),
            reason_code=data.get("reason_code"),
            missing_concepts=tuple(str(c) for c in concepts),
            question_intent=data.get("question_intent"),
            asked_at_revision=data.get("asked_at_revision"),
        )


@dataclass(frozen=True)
class CategoryResolution:
    """Category resolution slot inside active_need.

    `selected_category_id` accepts int (legacy int-id catalog) or str (UUID
    catalog from the dynamic category-catalog package). Optional catalog_id /
    catalog_revision / match_status track the dynamic catalog provenance and
    default to None so legacy tests remain compatible.
    """

    selected_category_id: Any = None
    candidates: tuple[dict[str, Any], ...] = ()
    catalog_id: str | None = None
    catalog_revision: int | None = None
    match_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_category_id": self.selected_category_id,
            "candidates": [dict(c) for c in self.candidates],
            "catalog_id": self.catalog_id,
            "catalog_revision": self.catalog_revision,
            "match_status": self.match_status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "CategoryResolution":
        if not data:
            return cls()
        candidates = data.get("candidates") or []
        revision_raw = data.get("catalog_revision")
        try:
            catalog_revision = (
                int(revision_raw) if revision_raw is not None else None
            )
        except (TypeError, ValueError):
            catalog_revision = None
        return cls(
            selected_category_id=data.get("selected_category_id"),
            candidates=tuple(dict(c) for c in candidates if isinstance(c, Mapping)),
            catalog_id=(
                str(data["catalog_id"]) if data.get("catalog_id") is not None else None
            ),
            catalog_revision=catalog_revision,
            match_status=(
                str(data["match_status"])
                if data.get("match_status") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class SemanticConstraint:
    """Single positive/negative/correction concept on the active need.

    The concept is a free-form Turkish/English string; the domain never
    stores a category id or enum (ADR-006). Provenance drives penalty
    weights inside the matcher.
    """

    concept: str
    provenance: str
    weight: float | None = None
    note_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "concept": self.concept,
            "provenance": self.provenance,
        }
        if self.weight is not None:
            payload["weight"] = float(self.weight)
        if self.note_hash:
            payload["note_hash"] = self.note_hash
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticConstraint":
        weight = data.get("weight")
        return cls(
            concept=str(data.get("concept") or "").strip(),
            provenance=str(data.get("provenance") or "INFERRED"),
            weight=float(weight) if weight is not None else None,
            note_hash=(
                str(data["note_hash"]) if data.get("note_hash") is not None else None
            ),
        )


@dataclass(frozen=True)
class SemanticConstraints:
    """Positive / negative / correction constraint buckets for the active need."""

    positive: tuple[SemanticConstraint, ...] = ()
    negative: tuple[SemanticConstraint, ...] = ()
    corrections: tuple[SemanticConstraint, ...] = ()

    def is_empty(self) -> bool:
        return not (self.positive or self.negative or self.corrections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "positive": [c.to_dict() for c in self.positive],
            "negative": [c.to_dict() for c in self.negative],
            "corrections": [c.to_dict() for c in self.corrections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SemanticConstraints":
        if not data:
            return cls()

        def _tuple(key: str) -> tuple[SemanticConstraint, ...]:
            items = data.get(key) or ()
            return tuple(
                SemanticConstraint.from_dict(item)
                for item in items
                if isinstance(item, Mapping)
            )

        return cls(
            positive=_tuple("positive"),
            negative=_tuple("negative"),
            corrections=_tuple("corrections"),
        )


@dataclass(frozen=True)
class ActiveNeed:
    need_id: str
    intent: dict[str, Any] = field(default_factory=dict)
    need_description: str = ""
    budget: dict[str, Any] = field(default_factory=dict)
    preferences: tuple[dict[str, Any], ...] = ()
    usage_context: tuple[str, ...] = ()
    entities: tuple[dict[str, Any], ...] = ()
    ambiguities: tuple[dict[str, Any], ...] = ()
    category_resolution: CategoryResolution = field(default_factory=CategoryResolution)
    confidence: float | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    semantic_constraints: SemanticConstraints = field(
        default_factory=SemanticConstraints
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "need_id": self.need_id,
            "intent": dict(self.intent),
            "need_description": self.need_description,
            "budget": dict(self.budget),
            "preferences": [dict(p) for p in self.preferences],
            "usage_context": list(self.usage_context),
            "entities": [dict(e) for e in self.entities],
            "ambiguities": [dict(a) for a in self.ambiguities],
            "category_resolution": self.category_resolution.to_dict(),
            "confidence": self.confidence,
            "signals": dict(self.signals),
            "semantic_constraints": self.semantic_constraints.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActiveNeed":
        return cls(
            need_id=str(data.get("need_id") or str(uuid4())),
            intent=dict(data.get("intent") or {}),
            need_description=str(data.get("need_description") or ""),
            budget=dict(data.get("budget") or {}),
            preferences=tuple(dict(p) for p in (data.get("preferences") or []) if isinstance(p, Mapping)),
            usage_context=tuple(str(x) for x in (data.get("usage_context") or [])),
            entities=tuple(dict(e) for e in (data.get("entities") or []) if isinstance(e, Mapping)),
            ambiguities=tuple(
                dict(a) for a in (data.get("ambiguities") or []) if isinstance(a, Mapping)
            ),
            category_resolution=CategoryResolution.from_dict(
                data.get("category_resolution")
            ),
            confidence=(
                float(data["confidence"]) if data.get("confidence") is not None else None
            ),
            signals=dict(data.get("signals") or {}),
            semantic_constraints=SemanticConstraints.from_dict(
                data.get("semantic_constraints")
            ),
        )

    @classmethod
    def empty(cls) -> "ActiveNeed":
        return cls(need_id=str(uuid4()))


@dataclass
class ConversationState:
    session_id: UUID
    schema_version: str = CURRENT_SCHEMA_VERSION
    revision: int = 0
    status: SessionStatus = SessionStatus.ACTIVE
    locale: str = "tr-TR"
    actor: Actor = field(default_factory=lambda: Actor(type=ActorType.ANONYMOUS))
    active_need: ActiveNeed | None = None
    resolved_context: dict[str, Any] = field(default_factory=dict)
    clarification: ClarificationState = field(default_factory=ClarificationState)
    last_client_message_id: str | None = None
    last_client_sequence: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    absolute_expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload_dict(self) -> dict[str, Any]:
        """Canonical payload embedded in Redis hash field `payload`."""
        return {
            "session_id": str(self.session_id),
            "schema_version": self.schema_version,
            "revision": self.revision,
            "status": self.status.value,
            "locale": self.locale,
            "actor": self.actor.to_dict(),
            "active_need": self.active_need.to_dict() if self.active_need else None,
            "resolved_context": dict(self.resolved_context),
            "clarification": self.clarification.to_dict(),
            "last_client_message_id": self.last_client_message_id,
            "last_client_sequence": self.last_client_sequence,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "expires_at": _iso(self.expires_at),
            "absolute_expires_at": _iso(self.absolute_expires_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload_dict(cls, data: Mapping[str, Any]) -> "ConversationState":
        need_raw = data.get("active_need")
        return cls(
            session_id=UUID(str(data["session_id"])),
            schema_version=str(data.get("schema_version") or CURRENT_SCHEMA_VERSION),
            revision=int(data.get("revision") or 0),
            status=SessionStatus(str(data.get("status") or SessionStatus.ACTIVE.value)),
            locale=str(data.get("locale") or "tr-TR"),
            actor=Actor.from_dict(data.get("actor") or {}),
            active_need=ActiveNeed.from_dict(need_raw) if isinstance(need_raw, Mapping) else None,
            resolved_context=dict(data.get("resolved_context") or {}),
            clarification=ClarificationState.from_dict(data.get("clarification")),
            last_client_message_id=data.get("last_client_message_id"),
            last_client_sequence=data.get("last_client_sequence"),
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
            expires_at=_parse_dt(data.get("expires_at")),
            absolute_expires_at=_parse_dt(data.get("absolute_expires_at")),
            metadata=dict(data.get("metadata") or {}),
        )

    def copy(self) -> "ConversationState":
        return ConversationState.from_payload_dict(self.to_payload_dict())


@dataclass(frozen=True)
class CompareAndSetResult:
    status: CasStatus
    session_id: UUID | None = None
    revision: int | None = None
    client_message_id: str | None = None
    state: ConversationState | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ConversationStateChangedEvent:
    event_id: str
    session_id: UUID
    previous_revision: int
    new_revision: int
    event_type: str
    operation_types: tuple[str, ...]
    correlation_id: str | None
    occurred_at: datetime

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": str(self.session_id),
            "previous_revision": self.previous_revision,
            "new_revision": self.new_revision,
            "event_type": self.event_type,
            "operation_types": list(self.operation_types),
            "correlation_id": self.correlation_id,
            "occurred_at": _iso(self.occurred_at),
        }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not value:
        return datetime.now(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text)
