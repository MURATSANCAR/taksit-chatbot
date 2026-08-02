"""Canonical search-plan models (query_planning v1)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


PLAN_VERSION = "v1"


class RequestType(str, Enum):
    SINGLE_PRODUCT_SEARCH = "SINGLE_PRODUCT_SEARCH"
    MULTI_ITEM_BUNDLE = "MULTI_ITEM_BUNDLE"
    PRODUCT_AND_CAMPAIGN_SEARCH = "PRODUCT_AND_CAMPAIGN_SEARCH"
    EXPLORATORY_SEARCH = "EXPLORATORY_SEARCH"
    COMPARISON = "COMPARISON"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class ConstraintStrength(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    OPTIONAL = "OPTIONAL"


class ConstraintOperator(str, Enum):
    EQ = "EQ"
    NEQ = "NEQ"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    IN = "IN"
    NOT_IN = "NOT_IN"
    CONTAINS = "CONTAINS"
    EXCLUDES = "EXCLUDES"
    RANGE = "RANGE"


class StateOperation(str, Enum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    REPLACE = "REPLACE"
    RELAX = "RELAX"
    REQUIRE = "REQUIRE"
    PREFER = "PREFER"
    TEMPORARY_EXCEPTION = "TEMPORARY_EXCEPTION"
    ROLLBACK = "ROLLBACK"
    CLEAR = "CLEAR"


# ── Plan constraint ────────────────────────────────────────────────


@dataclass
class PlanConstraint:
    constraint_id: str = ""
    dimension: str = ""
    operator: ConstraintOperator = ConstraintOperator.EQ
    value: Any = None
    unit: Optional[str] = None
    strength: ConstraintStrength = ConstraintStrength.SOFT
    priority: int = 0
    source_text: str = ""
    confidence: float = 0.0
    source: str = "fast_parser"
    scope: str = "item"

    def __post_init__(self) -> None:
        if not self.constraint_id:
            self.constraint_id = f"c-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "dimension": self.dimension,
            "operator": self.operator.value if isinstance(self.operator, Enum) else self.operator,
            "value": self.value,
            "unit": self.unit,
            "strength": self.strength.value if isinstance(self.strength, Enum) else self.strength,
            "priority": self.priority,
            "source_text": self.source_text,
            "confidence": self.confidence,
            "source": self.source,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanConstraint:
        return cls(
            constraint_id=data.get("constraint_id", ""),
            dimension=data.get("dimension", ""),
            operator=ConstraintOperator(data["operator"]) if data.get("operator") else ConstraintOperator.EQ,
            value=data.get("value"),
            unit=data.get("unit"),
            strength=ConstraintStrength(data["strength"]) if data.get("strength") else ConstraintStrength.SOFT,
            priority=int(data.get("priority", 0)),
            source_text=data.get("source_text", ""),
            confidence=float(data.get("confidence", 0.0)),
            source=data.get("source", "fast_parser"),
            scope=data.get("scope", "item"),
        )


# ── Conditional exception ──────────────────────────────────────────


@dataclass
class ConditionalException:
    exception_id: str = ""
    target_constraint_id: str = ""
    condition_code: str = ""
    stretch_value: Any = None
    policy_thresholds: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.exception_id:
            self.exception_id = f"exc-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "exception_id": self.exception_id,
            "target_constraint_id": self.target_constraint_id,
            "condition_code": self.condition_code,
            "stretch_value": self.stretch_value,
            "policy_thresholds": self.policy_thresholds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConditionalException:
        return cls(
            exception_id=data.get("exception_id", ""),
            target_constraint_id=data.get("target_constraint_id", ""),
            condition_code=data.get("condition_code", ""),
            stretch_value=data.get("stretch_value"),
            policy_thresholds=data.get("policy_thresholds"),
        )


# ── Category ref ───────────────────────────────────────────────────


@dataclass
class CategoryRef:
    resolved_id: Optional[str] = None
    raw_text: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"resolved_id": self.resolved_id, "raw_text": self.raw_text, "confidence": self.confidence}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CategoryRef:
        return cls(
            resolved_id=data.get("resolved_id"),
            raw_text=data.get("raw_text", ""),
            confidence=float(data.get("confidence", 0.0)),
        )


# ── Plan item ──────────────────────────────────────────────────────


@dataclass
class PlanItem:
    item_id: str = ""
    category: Optional[CategoryRef] = None
    hard_constraints: list[PlanConstraint] = field(default_factory=list)
    soft_preferences: list[PlanConstraint] = field(default_factory=list)
    excluded_constraints: list[PlanConstraint] = field(default_factory=list)
    conditional_exceptions: list[ConditionalException] = field(default_factory=list)
    ranking_priorities: list[str] = field(default_factory=list)
    unsupported_dimensions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.item_id:
            self.item_id = f"item-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "category": self.category.to_dict() if self.category else None,
            "hard_constraints": [c.to_dict() for c in self.hard_constraints],
            "soft_preferences": [c.to_dict() for c in self.soft_preferences],
            "excluded_constraints": [c.to_dict() for c in self.excluded_constraints],
            "conditional_exceptions": [e.to_dict() for e in self.conditional_exceptions],
            "ranking_priorities": list(self.ranking_priorities),
            "unsupported_dimensions": list(self.unsupported_dimensions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanItem:
        cat_data = data.get("category")
        return cls(
            item_id=data.get("item_id", ""),
            category=CategoryRef.from_dict(cat_data) if cat_data else None,
            hard_constraints=[PlanConstraint.from_dict(c) for c in data.get("hard_constraints") or []],
            soft_preferences=[PlanConstraint.from_dict(c) for c in data.get("soft_preferences") or []],
            excluded_constraints=[PlanConstraint.from_dict(c) for c in data.get("excluded_constraints") or []],
            conditional_exceptions=[ConditionalException.from_dict(e) for e in data.get("conditional_exceptions") or []],
            ranking_priorities=list(data.get("ranking_priorities") or []),
            unsupported_dimensions=list(data.get("unsupported_dimensions") or []),
        )


# ── Global constraints ────────────────────────────────────────────


@dataclass
class BudgetConstraint:
    minimum: Optional[float] = None
    target_maximum: Optional[float] = None
    stretch_maximum: Optional[float] = None
    currency: str = "TRY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum": self.minimum,
            "target_maximum": self.target_maximum,
            "stretch_maximum": self.stretch_maximum,
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BudgetConstraint:
        return cls(
            minimum=data.get("minimum"),
            target_maximum=data.get("target_maximum"),
            stretch_maximum=data.get("stretch_maximum"),
            currency=data.get("currency", "TRY"),
        )


@dataclass
class GlobalConstraints:
    budget: Optional[BudgetConstraint] = None
    allowed_merchants: list[str] = field(default_factory=list)
    excluded_merchants: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget.to_dict() if self.budget else None,
            "allowed_merchants": list(self.allowed_merchants),
            "excluded_merchants": list(self.excluded_merchants),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalConstraints:
        budget_data = data.get("budget")
        return cls(
            budget=BudgetConstraint.from_dict(budget_data) if budget_data else None,
            allowed_merchants=list(data.get("allowed_merchants") or []),
            excluded_merchants=list(data.get("excluded_merchants") or []),
        )


# ── Campaign intent ───────────────────────────────────────────────


@dataclass
class CampaignIntent:
    requested: bool = False
    required: bool = False
    institution_preferences: list[str] = field(default_factory=list)
    requested_terms: list[int] = field(default_factory=list)
    zero_rate_preferred: bool = False
    ranking_mode: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "required": self.required,
            "institution_preferences": list(self.institution_preferences),
            "requested_terms": list(self.requested_terms),
            "zero_rate_preferred": self.zero_rate_preferred,
            "ranking_mode": self.ranking_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignIntent:
        return cls(
            requested=bool(data.get("requested", False)),
            required=bool(data.get("required", False)),
            institution_preferences=list(data.get("institution_preferences") or []),
            requested_terms=[int(t) for t in data.get("requested_terms") or []],
            zero_rate_preferred=bool(data.get("zero_rate_preferred", False)),
            ranking_mode=data.get("ranking_mode"),
        )


# ── Source info ────────────────────────────────────────────────────


@dataclass
class PlanSource:
    fast_parser_used: bool = True
    llm_patch_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"fast_parser_used": self.fast_parser_used, "llm_patch_used": self.llm_patch_used}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanSource:
        return cls(
            fast_parser_used=bool(data.get("fast_parser_used", True)),
            llm_patch_used=bool(data.get("llm_patch_used", False)),
        )


# ── Canonical search plan ─────────────────────────────────────────


@dataclass
class CanonicalSearchPlan:
    plan_version: str = PLAN_VERSION
    request_type: RequestType = RequestType.SINGLE_PRODUCT_SEARCH
    items: list[PlanItem] = field(default_factory=list)
    global_constraints: Optional[GlobalConstraints] = None
    campaign_intent: Optional[CampaignIntent] = None
    ambiguities: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    clarification_required: bool = False
    clarification_questions: list[dict[str, Any]] = field(default_factory=list)
    unsupported_capabilities: list[str] = field(default_factory=list)
    source: Optional[PlanSource] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "request_type": self.request_type.value if isinstance(self.request_type, Enum) else self.request_type,
            "items": [i.to_dict() for i in self.items],
            "global_constraints": self.global_constraints.to_dict() if self.global_constraints else None,
            "campaign_intent": self.campaign_intent.to_dict() if self.campaign_intent else None,
            "ambiguities": list(self.ambiguities),
            "conflicts": list(self.conflicts),
            "clarification_required": self.clarification_required,
            "clarification_questions": list(self.clarification_questions),
            "unsupported_capabilities": list(self.unsupported_capabilities),
            "source": self.source.to_dict() if self.source else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalSearchPlan:
        gc_data = data.get("global_constraints")
        ci_data = data.get("campaign_intent")
        src_data = data.get("source")
        return cls(
            plan_version=data.get("plan_version", PLAN_VERSION),
            request_type=RequestType(data["request_type"]) if data.get("request_type") else RequestType.SINGLE_PRODUCT_SEARCH,
            items=[PlanItem.from_dict(i) for i in data.get("items") or []],
            global_constraints=GlobalConstraints.from_dict(gc_data) if gc_data else None,
            campaign_intent=CampaignIntent.from_dict(ci_data) if ci_data else None,
            ambiguities=list(data.get("ambiguities") or []),
            conflicts=list(data.get("conflicts") or []),
            clarification_required=bool(data.get("clarification_required", False)),
            clarification_questions=list(data.get("clarification_questions") or []),
            unsupported_capabilities=list(data.get("unsupported_capabilities") or []),
            source=PlanSource.from_dict(src_data) if src_data else None,
        )


# ── State operation record ─────────────────────────────────────────


@dataclass
class StateOperationRecord:
    operation: StateOperation
    target_constraint_id: str
    value: Any = None
    query_version: int = 0
    previous_state_snapshot: Optional[dict[str, Any]] = None
    previous_plan_snapshot: Optional[dict[str, Any]] = None
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value if isinstance(self.operation, Enum) else self.operation,
            "target_constraint_id": self.target_constraint_id,
            "value": self.value,
            "query_version": self.query_version,
            "previous_state_snapshot": self.previous_state_snapshot,
            "previous_plan_snapshot": self.previous_plan_snapshot,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateOperationRecord:
        return cls(
            operation=StateOperation(data["operation"]) if data.get("operation") else StateOperation.ADD,
            target_constraint_id=data.get("target_constraint_id", ""),
            value=data.get("value"),
            query_version=int(data.get("query_version", 0)),
            previous_state_snapshot=data.get("previous_state_snapshot"),
            previous_plan_snapshot=data.get("previous_plan_snapshot"),
            timestamp=data.get("timestamp", ""),
        )
