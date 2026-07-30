"""Semantic match policy domain + storage compatibility mapper.

Canonical domain fields (ADR-005 / policy contract):

    minimum_candidate_score
    minimum_auto_select_score
    minimum_auto_select_gap
    maximum_candidates
    alias_weight / lexical_weight / vector_weight / use_case_weight / hierarchy_weight
    allow_lexical_degraded_mode
    exact_alias_can_auto_select
    maximum_embedding_timeout_ms
    policy_version

Storage (V008) may still use legacy column names
``minimum_score`` / ``clarify_score_gap``. The mapper translates both ways
without destructive renames.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping


# Storage / legacy → canonical
_LEGACY_TO_CANONICAL = {
    "minimum_score": "minimum_candidate_score",
    "minimum_match_score": "minimum_candidate_score",
    "clarify_score_gap": "minimum_auto_select_gap",
    "minimum_top_score_gap": "minimum_auto_select_gap",
}

# Canonical → preferred V008 storage columns (no destructive rename)
_CANONICAL_TO_STORAGE = {
    "minimum_candidate_score": "minimum_score",
    "minimum_auto_select_gap": "clarify_score_gap",
}


@dataclass(frozen=True)
class SemanticMatchPolicy:
    policy_code: str = "CATEGORY_MATCH_DEFAULT"
    minimum_candidate_score: float = 0.55
    minimum_auto_select_score: float = 0.72
    minimum_auto_select_gap: float = 0.08
    maximum_candidates: int = 3
    alias_weight: float = 0.35
    lexical_weight: float = 0.15
    vector_weight: float = 0.35
    use_case_weight: float = 0.10
    hierarchy_weight: float = 0.05
    allow_lexical_degraded_mode: bool = True
    exact_alias_can_auto_select: bool = True
    maximum_embedding_timeout_ms: int = 250
    cache_ttl_seconds: int = 300
    require_semantic_description: bool = True
    max_depth: int = 4
    fuzzy_min_similarity: float = 0.78
    policy_version: int = 1

    def __post_init__(self) -> None:
        if self.minimum_auto_select_score < self.minimum_candidate_score:
            raise ValueError(
                "minimum_auto_select_score must be >= minimum_candidate_score"
            )
        if not (0.0 <= self.minimum_auto_select_gap <= 1.0):
            raise ValueError("minimum_auto_select_gap must be in [0, 1]")
        if self.maximum_candidates < 1:
            raise ValueError("maximum_candidates must be positive")
        for name in (
            "alias_weight",
            "lexical_weight",
            "vector_weight",
            "use_case_weight",
            "hierarchy_weight",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")


class SemanticMatchPolicyMapper:
    """Map storage / admin payloads ↔ canonical SemanticMatchPolicy."""

    @classmethod
    def canonicalize_keys(cls, raw: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in raw.items():
            canon = _LEGACY_TO_CANONICAL.get(key, key)
            # Prefer already-canonical if both present
            if canon in out and key in _LEGACY_TO_CANONICAL:
                continue
            out[canon] = value
        return out

    @classmethod
    def from_storage(cls, row: Mapping[str, Any]) -> SemanticMatchPolicy:
        data = cls.canonicalize_keys(dict(row))
        allowed = {f.name for f in fields(SemanticMatchPolicy)}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        return SemanticMatchPolicy(**kwargs)

    @classmethod
    def to_storage(cls, policy: SemanticMatchPolicy) -> dict[str, Any]:
        raw = {
            f.name: getattr(policy, f.name) for f in fields(SemanticMatchPolicy)
        }
        stored: dict[str, Any] = {}
        for key, value in raw.items():
            stored[_CANONICAL_TO_STORAGE.get(key, key)] = value
        return stored


__all__ = ["SemanticMatchPolicy", "SemanticMatchPolicyMapper"]
