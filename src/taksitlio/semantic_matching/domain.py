"""Domain models for the semantic category matcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CategoryMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    CATALOG_EMPTY = "CATALOG_EMPTY"
    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"


@dataclass(frozen=True)
class SemanticMatchPolicy:
    policy_code: str = "CATEGORY_MATCH_DEFAULT"
    minimum_score: float = 0.55
    clarify_score_gap: float = 0.08
    maximum_candidates: int = 3
    alias_weight: float = 0.35
    lexical_weight: float = 0.15
    vector_weight: float = 0.35
    use_case_weight: float = 0.10
    hierarchy_weight: float = 0.05
    allow_lexical_degraded_mode: bool = True
    cache_ttl_seconds: int = 300
    require_semantic_description: bool = True
    max_depth: int = 4
    fuzzy_min_similarity: float = 0.78
    policy_version: int = 1


@dataclass(frozen=True)
class MatchQuery:
    text: str
    catalog_id: str
    locale: str
    embedding_profile_id: str
    catalog_revision: int
    session_id: Optional[str] = None
    extra_hints: tuple[str, ...] = ()


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
            "alias_text": self.alias_text,
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


__all__ = [
    "CategoryCandidate",
    "CategoryMatchDecision",
    "CategoryMatchResult",
    "CategoryMatchStatus",
    "MatchQuery",
    "SemanticMatchPolicy",
    "SignalBreakdown",
]
