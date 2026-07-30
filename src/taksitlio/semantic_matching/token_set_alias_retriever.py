"""Token-set alias retrieval — no substring matching (ADR-008 P0).

Signals:
    SURFACE_EXACT_PHRASE
    NORMALIZED_EXACT_PHRASE
    EXACT_TOKEN_SET
    PREFIX_SAFE_TOKEN_SET
    CHARACTER_NGRAM
    MORPHOLOGICAL_VARIANT
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence

from taksitlio.category_catalog.domain import CategorySnapshotNode, MatchMode
from taksitlio.semantic_matching.turkish_normalize import (
    ascii_fold,
    turkish_lower,
)


class AliasSignalKind(str, Enum):
    SURFACE_EXACT_PHRASE = "SURFACE_EXACT_PHRASE"
    NORMALIZED_EXACT_PHRASE = "NORMALIZED_EXACT_PHRASE"
    EXACT_TOKEN_SET = "EXACT_TOKEN_SET"
    PREFIX_SAFE_TOKEN_SET = "PREFIX_SAFE_TOKEN_SET"
    CHARACTER_NGRAM = "CHARACTER_NGRAM"
    MORPHOLOGICAL_VARIANT = "MORPHOLOGICAL_VARIANT"


@dataclass(frozen=True)
class TokenSetAliasHit:
    kind: AliasSignalKind
    score: float
    alias_text: str
    query_text: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "score": float(self.score),
            "alias_text": self.alias_text,
        }


@dataclass(frozen=True)
class TokenSetAliasScore:
    """Aggregated per-node alias channel scores (ADR-008)."""

    surface_exact: float = 0.0
    normalized_exact: float = 0.0
    token_set: float = 0.0
    prefix_safe: float = 0.0
    character_ngram: float = 0.0
    morphological_variant: float = 0.0
    best_hit: Optional[TokenSetAliasHit] = None
    hits: tuple[TokenSetAliasHit, ...] = ()

    @property
    def aggregate_alias(self) -> float:
        """Backward-compatible single alias score (max of channels)."""

        return max(
            self.surface_exact,
            self.normalized_exact,
            self.token_set,
            self.prefix_safe,
            self.character_ngram,
            self.morphological_variant,
        )

    @property
    def is_surface_exact(self) -> bool:
        return self.surface_exact >= 0.9

    @property
    def is_strong_exact(self) -> bool:
        return self.surface_exact >= 0.9 or self.normalized_exact >= 0.9


def _tokens(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    normalized = turkish_lower(text).replace(",", " ").replace(";", " ")
    return tuple(t for t in normalized.split() if len(t) > 1)


def _token_set(text: str) -> frozenset[str]:
    return frozenset(_tokens(text))


def _char_ngram_similarity(a: str, b: str, *, n: int = 3) -> float:
    if not a or not b:
        return 0.0

    def grams(text: str) -> set[str]:
        padded = f"  {text}  "
        return {padded[i : i + n] for i in range(len(padded) - n + 1)}

    ga, gb = grams(turkish_lower(a)), grams(turkish_lower(b))
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _prefix_safe_token_match(
    query_tok: str,
    alias_tok: str,
    *,
    max_extra: int = 4,
) -> bool:
    """True when one token is a prefix-safe inflection of the other.

    Requires shared prefix of length ≥ 4 and bounded suffix length.
    Rejects substring-in-the-middle cases (``masa`` vs ``masaüstü``).
    """

    q, a = turkish_lower(query_tok), turkish_lower(alias_tok)
    if not q or not a or q == a:
        return q == a
    shorter, longer = (q, a) if len(q) <= len(a) else (a, q)
    if len(shorter) < 4:
        return False
    if not longer.startswith(shorter):
        return False
    return (len(longer) - len(shorter)) <= max_extra


class TokenSetAliasRetriever:
    """Score a node against one or more query concept variants.

    Never uses bare substring containment (``alias in query``).
    """

    def __init__(
        self,
        *,
        character_ngram_min_similarity: float = 0.78,
        character_ngram_min_token_length: int = 4,
        morphological_variant_min_length: int = 4,
    ) -> None:
        self._ngram_min = max(0.0, min(1.0, float(character_ngram_min_similarity)))
        self._ngram_min_len = max(2, int(character_ngram_min_token_length))
        self._morph_min = max(2, int(morphological_variant_min_length))

    def score(
        self,
        query_variants: Sequence[str],
        node: CategorySnapshotNode,
        *,
        morphological_query_variants: Sequence[str] = (),
    ) -> TokenSetAliasScore:
        alias_texts = self._alias_texts(node)
        if not alias_texts or not query_variants:
            return TokenSetAliasScore()

        hits: list[TokenSetAliasHit] = []
        surface_exact = 0.0
        normalized_exact = 0.0
        token_set = 0.0
        prefix_safe = 0.0
        ngram = 0.0
        morph = 0.0

        primary = query_variants[0]
        for q in query_variants:
            q_norm = turkish_lower(q)
            q_fold = ascii_fold(q_norm)
            q_set = _token_set(q)
            for alias in alias_texts:
                a_norm = turkish_lower(alias)
                a_fold = ascii_fold(a_norm)
                a_set = _token_set(alias)

                # 1) Exact phrase (surface / normalized / ascii-fold).
                if q_norm == a_norm:
                    score = 1.0
                    kind = AliasSignalKind.SURFACE_EXACT_PHRASE
                    surface_exact = max(surface_exact, score)
                    hits.append(TokenSetAliasHit(kind, score, alias, q))
                    continue
                if q_fold == a_fold and q_fold:
                    score = 0.96
                    kind = AliasSignalKind.NORMALIZED_EXACT_PHRASE
                    normalized_exact = max(normalized_exact, score)
                    hits.append(TokenSetAliasHit(kind, score, alias, q))
                    continue

                # Whole-token membership for single-token alias in multi-token query.
                q_tokens = _tokens(q)
                if len(a_set) == 1 and next(iter(a_set)) in q_tokens:
                    score = 0.92
                    kind = AliasSignalKind.SURFACE_EXACT_PHRASE
                    surface_exact = max(surface_exact, score)
                    hits.append(TokenSetAliasHit(kind, score, alias, q))
                    continue

                # 2) Exact token-set (same multiset of tokens, any order).
                # Single-token query must not exact-match a multi-token alias.
                if q_set and a_set and q_set == a_set and len(q_set) == len(a_set):
                    score = 0.90
                    kind = AliasSignalKind.EXACT_TOKEN_SET
                    token_set = max(token_set, score)
                    hits.append(TokenSetAliasHit(kind, score, alias, q))
                    continue

                # 3) Prefix-safe token-set (pairwise prefix inflection).
                if self._prefix_safe_sets(q_set, a_set):
                    score = 0.82
                    kind = AliasSignalKind.PREFIX_SAFE_TOKEN_SET
                    prefix_safe = max(prefix_safe, score)
                    hits.append(TokenSetAliasHit(kind, score, alias, q))
                    continue

                # 4) Character n-gram — typo / fuzzy only, never auto-select alone.
                if (
                    len(q_norm) >= self._ngram_min_len
                    and len(a_norm) >= self._ngram_min_len
                ):
                    sim = _char_ngram_similarity(q_norm, a_norm)
                    if sim >= self._ngram_min:
                        score = min(0.75, sim)
                        kind = AliasSignalKind.CHARACTER_NGRAM
                        ngram = max(ngram, score)
                        hits.append(TokenSetAliasHit(kind, score, alias, q))

        # 5) Morphological variants of the query (weaker channel).
        for mq in morphological_query_variants:
            if len(mq) < self._morph_min:
                continue
            mq_norm = turkish_lower(mq)
            for alias in alias_texts:
                a_norm = turkish_lower(alias)
                if mq_norm == a_norm or _token_set(mq) == _token_set(alias):
                    score = 0.65
                    morph = max(morph, score)
                    hits.append(
                        TokenSetAliasHit(
                            AliasSignalKind.MORPHOLOGICAL_VARIANT,
                            score,
                            alias,
                            mq,
                        )
                    )

        best = max(hits, key=lambda h: h.score) if hits else None
        return TokenSetAliasScore(
            surface_exact=surface_exact,
            normalized_exact=normalized_exact,
            token_set=token_set,
            prefix_safe=prefix_safe,
            character_ngram=ngram,
            morphological_variant=morph,
            best_hit=best,
            hits=tuple(hits),
        )

    def matches_negative_hard_exclude(
        self,
        concept_variants: Sequence[str],
        node: CategorySnapshotNode,
    ) -> bool:
        """Hard-exclude on surface / normalized / exact token-set / token membership.

        Character n-gram and weak morphology never hard-exclude alone.
        Token membership: a single-token negative that is an *exact token*
        of a multi-token alias (``saat`` ∈ ``akıllı saat``) hard-excludes —
        this is token-boundary safe, not substring (``masa`` ∉ ``masaüstü``).
        """

        score = self.score(concept_variants, node)
        if (
            score.surface_exact >= 0.9
            or score.normalized_exact >= 0.9
            or score.token_set >= 0.9
        ):
            return True
        alias_texts = self._alias_texts(node)
        alias_tokens: set[str] = set()
        for alias in alias_texts:
            alias_tokens.update(_tokens(alias))
        for concept in concept_variants:
            c_tokens = _tokens(concept)
            if len(c_tokens) == 1 and c_tokens[0] in alias_tokens:
                return True
        return False

    @staticmethod
    def _alias_texts(node: CategorySnapshotNode) -> list[str]:
        out: list[str] = []
        for alias in node.aliases:
            if alias.alias_text:
                out.append(alias.alias_text)
        out.extend(node.synonyms)
        if node.display_name:
            out.append(node.display_name)
        return out

    @staticmethod
    def _prefix_safe_sets(q_set: frozenset[str], a_set: frozenset[str]) -> bool:
        if not q_set or not a_set or len(q_set) != len(a_set):
            return False
        # Every alias token must pair with a unique query token via prefix-safe.
        unused = set(q_set)
        for alias_tok in a_set:
            matched = None
            for q_tok in unused:
                if _prefix_safe_token_match(q_tok, alias_tok):
                    matched = q_tok
                    break
            if matched is None:
                return False
            unused.remove(matched)
        return True


__all__ = [
    "AliasSignalKind",
    "TokenSetAliasHit",
    "TokenSetAliasRetriever",
    "TokenSetAliasScore",
]
