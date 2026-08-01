"""Inverted alias index for O(tokens) catalog entity lookup (fast_parse hot path).

Avoids scanning thousands of category aliases with normalize_turkish per request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from taksitlio.entity_resolution import EntityCandidate
from taksitlio.semantic_matching.turkish_normalize import turkish_lower

_NON_WORD_RE = re.compile(r"[^0-9a-zçğıöşü]+", re.IGNORECASE)


def fold_alias(text: str) -> str:
    return " ".join(_NON_WORD_RE.sub(" ", turkish_lower(text or "")).split())


def utterance_keys(text: str, *, max_width: int = 4) -> set[str]:
    """Unigrams + short phrases present in the utterance (token-boundary safe)."""

    tokens = fold_alias(text).split()
    keys: set[str] = set()
    for i, tok in enumerate(tokens):
        if len(tok) >= 3:
            keys.add(tok)
        for width in range(2, max_width + 1):
            if i + width <= len(tokens):
                keys.add(" ".join(tokens[i : i + width]))
    return keys


@dataclass(frozen=True)
class CatalogAliasIndex:
    category_phrases: dict[str, tuple[EntityCandidate, ...]]
    merchant_phrases: dict[str, tuple[EntityCandidate, ...]]
    brand_phrases: dict[str, tuple[EntityCandidate, ...]]

    def lookup_categories(self, text: str) -> tuple[EntityCandidate, ...]:
        return self._lookup(self.category_phrases, text)

    def lookup_merchants(self, text: str) -> tuple[EntityCandidate, ...]:
        return self._lookup(self.merchant_phrases, text)

    def lookup_brands(self, text: str) -> tuple[EntityCandidate, ...]:
        return self._lookup(self.brand_phrases, text)

    @staticmethod
    def _lookup(
        phrases: dict[str, tuple[EntityCandidate, ...]], text: str
    ) -> tuple[EntityCandidate, ...]:
        keys = utterance_keys(text)
        if not keys:
            return ()
        out: list[EntityCandidate] = []
        seen: set[str] = set()
        for key in keys:
            for cand in phrases.get(key, ()):
                eid = str(cand.entity_id or cand.display_name)
                if eid in seen:
                    continue
                seen.add(eid)
                out.append(cand)
        return tuple(out)


def _index_entities(
    entities: Sequence[EntityCandidate],
) -> dict[str, tuple[EntityCandidate, ...]]:
    buckets: dict[str, list[EntityCandidate]] = {}
    for cand in entities:
        names = (
            cand.display_name,
            cand.canonical_name,
            *(cand.aliases or ()),
        )
        seen_alias: set[str] = set()
        for name in names:
            folded = fold_alias(str(name or ""))
            if len(folded) < 3 or folded in seen_alias:
                continue
            seen_alias.add(folded)
            buckets.setdefault(folded, []).append(cand)
    return {k: tuple(dict.fromkeys(v)) for k, v in buckets.items()}


def build_alias_index(
    *,
    categories: Sequence[EntityCandidate] = (),
    merchants: Sequence[EntityCandidate] = (),
    brands: Sequence[EntityCandidate] = (),
) -> CatalogAliasIndex:
    return CatalogAliasIndex(
        category_phrases=_index_entities(categories),
        merchant_phrases=_index_entities(merchants),
        brand_phrases=_index_entities(brands),
    )


def matched_alias_labels(
    cand: EntityCandidate, text: str
) -> tuple[str, ...]:
    """Aliases of cand that actually appear in text (for search terms)."""

    u_keys = utterance_keys(text)
    out: list[str] = []
    display = str(cand.display_name or "").strip()
    if display:
        out.append(display)
    for name in (cand.display_name, cand.canonical_name, *(cand.aliases or ())):
        raw = str(name or "").strip()
        folded = fold_alias(raw)
        if folded and folded in u_keys and raw not in out:
            out.append(raw)
    return tuple(out)


__all__ = [
    "CatalogAliasIndex",
    "build_alias_index",
    "fold_alias",
    "matched_alias_labels",
    "utterance_keys",
]
