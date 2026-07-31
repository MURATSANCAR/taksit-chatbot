"""Dynamic fuzzy entity resolution (ADR-010 §32 / §58).

Candidates come from the catalog — never from hardcoded typo maps.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional, Sequence

from taksitlio.semantic_matching.turkish_normalize import (
    jaccard,
    normalize_turkish,
)


class MatchType(str, Enum):
    EXACT_CANONICAL = "EXACT_CANONICAL"
    EXACT_ALIAS = "EXACT_ALIAS"
    NORMALIZED_EXACT = "NORMALIZED_EXACT"
    TOKEN_SET = "TOKEN_SET"
    TRIGRAM = "TRIGRAM"
    EDIT_DISTANCE = "EDIT_DISTANCE"
    CHARACTER_NGRAM = "CHARACTER_NGRAM"


class ResolutionAction(str, Enum):
    AUTO_SELECT = "AUTO_SELECT"
    CLARIFY = "CLARIFY"
    MULTI_OR_ROUTE = "MULTI_OR_ROUTE"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class EntityCandidate:
    entity_id: str
    display_name: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    entity_type: str = "unknown"  # merchant | institution | brand | category | ...


@dataclass(frozen=True)
class ScoredCandidate:
    entity_id: str
    display_name: str
    match_type: MatchType
    similarity: float
    confidence: float


@dataclass(frozen=True)
class ResolutionPolicy:
    auto_select_min: float = 0.92
    clarify_min: float = 0.78
    max_candidates: int = 5
    min_candidate_gap: float = 0.05


@dataclass(frozen=True)
class ResolutionResult:
    input_text: str
    action: ResolutionAction
    resolved_entity_id: Optional[str]
    resolved_display_name: Optional[str]
    match_type: Optional[MatchType]
    similarity: Optional[float]
    confidence: Optional[float]
    candidate_gap: Optional[float]
    candidates: tuple[ScoredCandidate, ...]


def _token_set_ratio(a: str, b: str) -> float:
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def score_candidate(query: str, candidate: EntityCandidate) -> ScoredCandidate:
    q = normalize_turkish(query)
    names = (candidate.canonical_name, candidate.display_name, *candidate.aliases)
    best_type = MatchType.TRIGRAM
    best_sim = 0.0
    best_conf = 0.0

    for name in names:
        if not name:
            continue
        n = normalize_turkish(name)
        if q.value and q.value == n.value:
            # Distinguish canonical vs alias exact.
            is_alias = name not in (candidate.canonical_name, candidate.display_name)
            mtype = MatchType.EXACT_ALIAS if is_alias else MatchType.EXACT_CANONICAL
            return ScoredCandidate(
                entity_id=candidate.entity_id,
                display_name=candidate.display_name,
                match_type=mtype,
                similarity=1.0,
                confidence=0.99 if mtype is MatchType.EXACT_CANONICAL else 0.97,
            )
        if q.ascii_fold and q.ascii_fold == n.ascii_fold:
            sim, conf, mtype = 0.98, 0.95, MatchType.NORMALIZED_EXACT
        else:
            token = _token_set_ratio(q.value, n.value)
            tri = max(
                jaccard(q.trigrams, n.trigrams),
                jaccard(q.ascii_trigrams, n.ascii_trigrams),
            )
            edit = SequenceMatcher(None, q.ascii_fold or q.value, n.ascii_fold or n.value).ratio()
            # Weighted blend; no business-specific boosts.
            sim = max(token, tri, edit)
            if token >= tri and token >= edit and token > 0:
                mtype = MatchType.TOKEN_SET
                conf = 0.75 + 0.2 * token
            elif tri >= edit:
                mtype = MatchType.TRIGRAM if len(q.trigrams) >= 3 else MatchType.CHARACTER_NGRAM
                conf = 0.70 + 0.25 * tri
            else:
                mtype = MatchType.EDIT_DISTANCE
                conf = 0.65 + 0.30 * edit
            sim = float(sim)
            conf = min(0.99, float(conf))

        if conf > best_conf:
            best_conf = conf
            best_sim = sim
            best_type = mtype

    return ScoredCandidate(
        entity_id=candidate.entity_id,
        display_name=candidate.display_name,
        match_type=best_type,
        similarity=best_sim,
        confidence=best_conf,
    )


def resolve_entity(
    query: str,
    catalog: Sequence[EntityCandidate],
    *,
    policy: Optional[ResolutionPolicy] = None,
) -> ResolutionResult:
    pol = policy or ResolutionPolicy()
    text = (query or "").strip()
    if not text or not catalog:
        return ResolutionResult(
            input_text=text,
            action=ResolutionAction.UNRESOLVED,
            resolved_entity_id=None,
            resolved_display_name=None,
            match_type=None,
            similarity=None,
            confidence=None,
            candidate_gap=None,
            candidates=(),
        )

    scored = sorted(
        (score_candidate(text, c) for c in catalog),
        key=lambda s: s.confidence,
        reverse=True,
    )
    top = scored[: pol.max_candidates]
    best = top[0]
    second = top[1] if len(top) > 1 else None
    gap = None if second is None else best.confidence - second.confidence

    if best.confidence >= pol.auto_select_min and (
        gap is None or gap >= pol.min_candidate_gap
    ):
        action = ResolutionAction.AUTO_SELECT
        return ResolutionResult(
            input_text=text,
            action=action,
            resolved_entity_id=best.entity_id,
            resolved_display_name=best.display_name,
            match_type=best.match_type,
            similarity=best.similarity,
            confidence=best.confidence,
            candidate_gap=gap,
            candidates=tuple(top),
        )

    if best.confidence >= pol.clarify_min:
        return ResolutionResult(
            input_text=text,
            action=ResolutionAction.CLARIFY,
            resolved_entity_id=None,
            resolved_display_name=None,
            match_type=best.match_type,
            similarity=best.similarity,
            confidence=best.confidence,
            candidate_gap=gap,
            candidates=tuple(top),
        )

    if best.confidence > 0:
        return ResolutionResult(
            input_text=text,
            action=ResolutionAction.MULTI_OR_ROUTE,
            resolved_entity_id=None,
            resolved_display_name=None,
            match_type=best.match_type,
            similarity=best.similarity,
            confidence=best.confidence,
            candidate_gap=gap,
            candidates=tuple(top),
        )

    return ResolutionResult(
        input_text=text,
        action=ResolutionAction.UNRESOLVED,
        resolved_entity_id=None,
        resolved_display_name=None,
        match_type=None,
        similarity=None,
        confidence=None,
        candidate_gap=None,
        candidates=tuple(top),
    )


__all__ = [
    "EntityCandidate",
    "MatchType",
    "ResolutionAction",
    "ResolutionPolicy",
    "ResolutionResult",
    "ScoredCandidate",
    "resolve_entity",
    "score_candidate",
]
