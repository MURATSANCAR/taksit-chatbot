"""Domain models for the semantic category matcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from taksitlio.semantic_matching.policy import (
    SemanticMatchPolicy,
    SemanticMatchPolicyMapper,
)


class CategoryMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    CATALOG_EMPTY = "CATALOG_EMPTY"
    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"


@dataclass(frozen=True)
class MatchQuery:
    """Matcher input from ConversationState snapshot — not raw chat transcript."""

    catalog_id: str
    locale: str
    embedding_profile_id: str
    need_description: str = ""
    catalog_revision: int = 0
    session_id: Optional[str] = None
    preferences: tuple[Mapping[str, Any], ...] = ()
    usage_context: tuple[str, ...] = ()
    deadline_ms: Optional[int] = None
    extra_hints: tuple[str, ...] = ()
    # Legacy constructor alias (tests / early callers)
    text: str = ""

    def __post_init__(self) -> None:
        desc = (self.need_description or self.text or "").strip()
        if not desc:
            raise ValueError("need_description is required")
        if not self.need_description:
            object.__setattr__(self, "need_description", desc)
        if not self.text:
            object.__setattr__(self, "text", desc)

    @property
    def query_text(self) -> str:
        return self.need_description

    @property
    def hint_texts(self) -> tuple[str, ...]:
        prefs = tuple(
            str(p.get("concept"))
            for p in self.preferences
            if isinstance(p, Mapping) and p.get("concept")
        )
        return tuple(h for h in (self.extra_hints + prefs + self.usage_context) if h)


@dataclass(frozen=True)
class SignalBreakdown:
    alias: float = 0.0
    lexical: float = 0.0
    vector: float = 0.0
    use_case: float = 0.0
    hierarchy: float = 0.0
    alias_mode: Optional[str] = None
    alias_text: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "alias": self.alias,
            "lexical": self.lexical,
            "vector": self.vector,
            "use_case": self.use_case,
            "hierarchy": self.hierarchy,
            "alias_mode": self.alias_mode,
            # alias_text intentionally omitted from default dict (privacy)
        }


@dataclass(frozen=True)
class CategoryCandidate:
    category_id: str
    slug: str
    display_name: str
    score: float
    rank: int
    signals: SignalBreakdown

    def to_dict(self) -> dict:
        return {
            "category_id": self.category_id,
            "slug": self.slug,
            "display_name": self.display_name,
            "score": self.score,
            "rank": self.rank,
            "signals": self.signals.to_dict(),
        }


@dataclass(frozen=True)
class CategoryMatchDecision:
    status: CategoryMatchStatus
    selected_category_id: Optional[str]
    score_gap: Optional[float]
    reason: Optional[str] = None
    reason_code: Optional[str] = None
    missing_concepts: tuple[str, ...] = ()


@dataclass(frozen=True)
class CategoryMatchResult:
    query_text_hash: str
    catalog_id: str
    catalog_revision: int
    locale: str
    embedding_profile_id: str
    policy_code: str
    candidates: tuple[CategoryCandidate, ...]
    decision: CategoryMatchDecision
    duration_ms: float
    degraded: bool = False
    degraded_reasons: tuple[str, ...] = ()
    cache_hit: bool = False
    diagnostics: dict = field(default_factory=dict)

    @property
    def status(self) -> CategoryMatchStatus:
        return self.decision.status

    @property
    def selected_category_id(self) -> Optional[str]:
        return self.decision.selected_category_id

    @property
    def degraded_reason(self) -> Optional[str]:
        if not self.degraded_reasons:
            return None
        return self.degraded_reasons[0]


__all__ = [
    "CategoryCandidate",
    "CategoryMatchDecision",
    "CategoryMatchResult",
    "CategoryMatchStatus",
    "MatchQuery",
    "SemanticMatchPolicy",
    "SemanticMatchPolicyMapper",
    "SignalBreakdown",
]
