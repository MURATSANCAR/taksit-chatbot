"""Lexical scorer for alias / synonym matching (deterministic, no ML)."""

from __future__ import annotations

from dataclasses import dataclass

from taksitlio.category_catalog.domain import CategorySnapshotNode, MatchMode
from taksitlio.semantic_matching.domain import SignalBreakdown


@dataclass(frozen=True)
class AliasScore:
    score: float
    mode: MatchMode | None
    text: str | None


class AliasMatcher:
    """Scores a node against a normalized query using its alias / synonym set."""

    def __init__(self, fuzzy_min_similarity: float = 0.78) -> None:
        self._fuzzy_min = max(0.0, min(1.0, float(fuzzy_min_similarity)))

    def score(self, normalized_query: str, node: CategorySnapshotNode) -> AliasScore:
        if not normalized_query:
            return AliasScore(0.0, None, None)
        best = AliasScore(0.0, None, None)
        candidates: list[tuple[str, MatchMode, float]] = []
        for alias in node.aliases:
            candidates.append(
                (alias.alias_text, alias.alias_type, float(alias.weight))
            )
        # synonyms behave like EXACT lookups with weight 1.0
        for syn in node.synonyms:
            candidates.append((syn, MatchMode.EXACT, 1.0))
        # display_name acts as EXACT fallback with lower weight
        candidates.append((node.display_name, MatchMode.EXACT, 0.9))

        for raw_text, mode, weight in candidates:
            score = self._score_candidate(normalized_query, raw_text, mode)
            weighted = score * weight
            if weighted > best.score:
                best = AliasScore(weighted, mode, raw_text)
        return best

    def _score_candidate(
        self,
        normalized_query: str,
        raw_text: str,
        mode: MatchMode,
    ) -> float:
        text = (raw_text or "").casefold().strip()
        if not text:
            return 0.0
        if mode == MatchMode.EXACT:
            if text == normalized_query:
                return 1.0
            tokens = normalized_query.replace(",", " ").split()
            if text in tokens:
                return 0.92
            if text in normalized_query:
                return 0.82
            return 0.0
        if mode == MatchMode.PREFIX:
            if normalized_query.startswith(text) or text.startswith(normalized_query):
                return 0.85
            return 0.0
        if mode == MatchMode.FUZZY:
            similarity = _char_ngram_similarity(text, normalized_query, n=3)
            if similarity >= self._fuzzy_min:
                return min(1.0, similarity)
            return 0.0
        if mode == MatchMode.SEMANTIC_HINT:
            # semantic-hint aliases boost the semantic pool but never score
            # directly through the alias channel.
            if text in normalized_query:
                return 0.45
            return 0.0
        return 0.0


def _char_ngram_similarity(a: str, b: str, *, n: int = 3) -> float:
    if not a or not b:
        return 0.0

    def ngrams(text: str) -> set:
        padded = f"  {text}  "
        return {padded[i : i + n] for i in range(len(padded) - n + 1)}

    grams_a = ngrams(a)
    grams_b = ngrams(b)
    if not grams_a or not grams_b:
        return 0.0
    inter = grams_a & grams_b
    union = grams_a | grams_b
    return len(inter) / len(union)


class LexicalOverlapScorer:
    """Simple token overlap Jaccard scorer used as the lexical channel."""

    def score(self, normalized_query: str, node: CategorySnapshotNode) -> float:
        if not normalized_query:
            return 0.0
        query_tokens = _tokens(normalized_query)
        if not query_tokens:
            return 0.0
        pool: set[str] = set()
        pool.update(_tokens(node.display_name))
        pool.update(_tokens(node.description))
        pool.update(_tokens(node.semantic_description))
        for syn in node.synonyms:
            pool.update(_tokens(syn))
        for alias in node.aliases:
            pool.update(_tokens(alias.alias_text))
        if not pool:
            return 0.0
        inter = query_tokens & pool
        if not inter:
            return 0.0
        return len(inter) / len(query_tokens | pool)


class UseCaseScorer:
    def score(self, normalized_query: str, node: CategorySnapshotNode) -> float:
        if not normalized_query or not node.use_cases:
            return 0.0
        query_tokens = _tokens(normalized_query)
        if not query_tokens:
            return 0.0
        best = 0.0
        for uc in node.use_cases:
            uc_tokens = _tokens(uc.use_case_text)
            if not uc_tokens:
                continue
            inter = query_tokens & uc_tokens
            if inter:
                score = len(inter) / len(query_tokens | uc_tokens)
                best = max(best, score)
        return best


def _tokens(text: str) -> set:
    if not text:
        return set()
    normalized = text.casefold().replace(",", " ").replace(";", " ")
    return {t for t in normalized.split() if len(t) > 1}


def normalize_query(text: str, extra_hints: tuple = ()) -> str:
    parts = [text or ""]
    parts.extend(extra_hints)
    joined = " ".join(p for p in parts if p)
    return " ".join(joined.casefold().split())


def collapse_signal(*values: float) -> SignalBreakdown:
    """Test helper to build a SignalBreakdown from positional args."""

    alias, lexical, vector, use_case, hierarchy = (list(values) + [0.0] * 5)[:5]
    return SignalBreakdown(
        alias=alias,
        lexical=lexical,
        vector=vector,
        use_case=use_case,
        hierarchy=hierarchy,
    )


__all__ = [
    "AliasMatcher",
    "AliasScore",
    "LexicalOverlapScorer",
    "UseCaseScorer",
    "collapse_signal",
    "normalize_query",
]
