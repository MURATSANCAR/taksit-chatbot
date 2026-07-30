"""Domain models for the semantic category matcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

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
    # ADR-006 semantic constraints (positive/negative/corrections).
    semantic_constraints: dict = field(default_factory=dict)
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

    def _constraint_concepts(self, key: str) -> tuple[str, ...]:
        entries = self.semantic_constraints.get(key) if self.semantic_constraints else None
        if not entries:
            return ()
        out: list[str] = []
        for entry in entries:
            if isinstance(entry, Mapping):
                concept = entry.get("concept")
                if concept:
                    out.append(str(concept))
            elif entry:
                out.append(str(entry))
        return tuple(out)

    @property
    def positive_concepts(self) -> tuple[str, ...]:
        return self._constraint_concepts("positive")

    @property
    def negative_concepts(self) -> tuple[str, ...]:
        return self._constraint_concepts("negative")

    @property
    def corrections(self) -> tuple[str, ...]:
        return self._constraint_concepts("corrections")

    def negative_entries(self) -> tuple[Mapping[str, Any], ...]:
        entries = self.semantic_constraints.get("negative") if self.semantic_constraints else None
        return tuple(e for e in (entries or ()) if isinstance(e, Mapping))

    def correction_entries(self) -> tuple[Mapping[str, Any], ...]:
        entries = self.semantic_constraints.get("corrections") if self.semantic_constraints else None
        return tuple(e for e in (entries or ()) if isinstance(e, Mapping))

    # ------------------------------------------------------------------
    # ADR-008: constraint → query variant expansion.
    # ------------------------------------------------------------------

    def _entries(self, key: str) -> tuple[Mapping[str, Any], ...]:
        entries = self.semantic_constraints.get(key) if self.semantic_constraints else None
        return tuple(e for e in (entries or ()) if isinstance(e, Mapping))

    @staticmethod
    def _variants_from_entry(entry: Mapping[str, Any]) -> tuple[str, ...]:
        """Return surface / normalized / concept / variants[].value as strings."""

        out: list[str] = []
        for candidate in (
            entry.get("surface_form"),
            entry.get("normalized_form"),
            entry.get("concept"),
        ):
            if isinstance(candidate, str):
                s = candidate.strip()
                if s:
                    out.append(s)
        variants = entry.get("variants") or ()
        if isinstance(variants, (list, tuple)):
            for variant in variants:
                if isinstance(variant, Mapping):
                    value = variant.get("value")
                    if isinstance(value, str) and value.strip():
                        out.append(value.strip())
        return tuple(out)

    @staticmethod
    def _morph_variants_from_entry(entry: Mapping[str, Any]) -> tuple[str, ...]:
        variants = entry.get("variants") or ()
        if not isinstance(variants, (list, tuple)):
            return ()
        out: list[str] = []
        for variant in variants:
            if not isinstance(variant, Mapping):
                continue
            if str(variant.get("type") or "") != "MORPHOLOGICAL_VARIANT":
                continue
            value = variant.get("value")
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
        return tuple(out)

    @staticmethod
    def _dedupe_preserve_case(values: Iterable[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for v in values:
            if not v:
                continue
            key = v.casefold().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(v)
        return tuple(out)

    def positive_query_variants(self) -> tuple[str, ...]:
        """All positive-side surface / normalized / concept / variants strings."""

        collected: list[str] = []
        for entry in self._entries("positive"):
            collected.extend(self._variants_from_entry(entry))
        return self._dedupe_preserve_case(collected)

    def negative_query_variants(self) -> tuple[str, ...]:
        collected: list[str] = []
        for entry in self._entries("negative"):
            collected.extend(self._variants_from_entry(entry))
        return self._dedupe_preserve_case(collected)

    def correction_query_variants(self) -> tuple[str, ...]:
        """Return every correction ``previous_concept`` + ``previous_surface``."""

        collected: list[str] = []
        for entry in self._entries("corrections"):
            prev_concept = entry.get("previous_concept") or entry.get("concept")
            if isinstance(prev_concept, str) and prev_concept.strip():
                collected.append(prev_concept.strip())
            prev_surface = entry.get("previous_surface_form") or entry.get(
                "surface_form"
            )
            if isinstance(prev_surface, str) and prev_surface.strip():
                collected.append(prev_surface.strip())
        return self._dedupe_preserve_case(collected)

    def morphological_positive_variants(self) -> tuple[str, ...]:
        collected: list[str] = []
        for entry in self._entries("positive"):
            collected.extend(self._morph_variants_from_entry(entry))
        return self._dedupe_preserve_case(collected)

    @property
    def multi_need_signal(self) -> bool:
        """True when the caller signalled a multi-need utterance.

        Either explicit ``signals.multi_need`` key in ``semantic_constraints``,
        or two-or-more distinct positive concepts without any negation /
        correction (both survived validator merge) — a signal that the
        matcher should keep an AMBIGUOUS verdict when two candidates
        remain in a tie.
        """

        if not self.semantic_constraints:
            return False
        signals = self.semantic_constraints.get("signals")
        if isinstance(signals, Mapping) and bool(signals.get("multi_need")):
            return True
        positives = self.positive_concepts
        if len(positives) < 2:
            return False
        negatives = set(self.negative_concepts)
        distinct = {p for p in positives if p not in negatives}
        return len(distinct) >= 2 and not self.correction_entries()


@dataclass(frozen=True)
class SignalBreakdown:
    alias: float = 0.0
    lexical: float = 0.0
    vector: float = 0.0
    use_case: float = 0.0
    hierarchy: float = 0.0
    alias_mode: Optional[str] = None
    alias_text: Optional[str] = None
    # ADR-006 hardening signals.
    positive_vector_score: float = 0.0
    negative_vector_score: float = 0.0
    exact_negative_alias: bool = False
    explicit_correction_penalty: float = 0.0
    direct_alias_match: bool = False
    hierarchy_collapsed: bool = False
    # ADR-008 per-channel alias signals — populated by TokenSetAliasRetriever.
    # Kept as separate fields so decision policy can distinguish a strong
    # surface_exact hit from a weak character_ngram / morphological hit.
    surface_exact_alias: float = 0.0
    normalized_exact_alias: float = 0.0
    token_set_alias: float = 0.0
    prefix_safe_alias: float = 0.0
    character_ngram: float = 0.0
    morphological_variant: float = 0.0
    negative_penalty: float = 0.0

    def to_dict(self) -> dict:
        # alias_text intentionally omitted from default dict (privacy).
        return {
            "alias": self.alias,
            "lexical": self.lexical,
            "vector": self.vector,
            "use_case": self.use_case,
            "hierarchy": self.hierarchy,
            "alias_mode": self.alias_mode,
            "positive_vector_score": self.positive_vector_score,
            "negative_vector_score": self.negative_vector_score,
            "exact_negative_alias": self.exact_negative_alias,
            "explicit_correction_penalty": self.explicit_correction_penalty,
            "direct_alias_match": self.direct_alias_match,
            "hierarchy_collapsed": self.hierarchy_collapsed,
            "surface_exact_alias": self.surface_exact_alias,
            "normalized_exact_alias": self.normalized_exact_alias,
            "token_set_alias": self.token_set_alias,
            "prefix_safe_alias": self.prefix_safe_alias,
            "character_ngram": self.character_ngram,
            "morphological_variant": self.morphological_variant,
            "negative_penalty": self.negative_penalty,
        }


@dataclass(frozen=True)
class CategoryCandidate:
    category_id: str
    slug: str
    display_name: str
    score: float
    rank: int
    signals: SignalBreakdown
    matchable: bool = True

    def to_dict(self) -> dict:
        return {
            "category_id": self.category_id,
            "slug": self.slug,
            "display_name": self.display_name,
            "score": self.score,
            "rank": self.rank,
            "signals": self.signals.to_dict(),
            "matchable": self.matchable,
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
