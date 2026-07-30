"""Turkish-aware text normalization for the semantic matcher.

The helpers here are intentionally *content-blind* — they never reference
any category name, alias list, or business word list. All they do is
work around the well-known pitfalls of Turkish casefolding and produce
character n-grams for fuzzy matching. ADR-006 forbids the matcher from
carrying category keyword tables, so every routine in this module works
purely on the input string.

Behaviour:

* NFKC unicode normalization (compatibility decomposition + recomposition)
* Turkish-aware lowercasing:
    ``İ``  → ``i``, ``I`` → ``ı``, ``ı`` and ``i`` stay themselves.
* Whitespace + punctuation collapse to single spaces.
* Optional ascii-fold companion (``şoförüm`` → ``soforum``) — kept as a
  *second* representation, never replacing the original, so exact alias
  matches still work.
* Character trigrams over both representations for fuzzy scoring.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_TR_LOWER_MAP = {
    "İ": "i",
    "I": "ı",
}

_PUNCT_RE = re.compile(r"[\u2000-\u206F\u2E00-\u2E7F\u3000-\u303F"
                       r"!-/:-@\[-`{-~“”‘’«»…·¡¿]")
_WS_RE = re.compile(r"\s+")

# ASCII-fold table for the common Turkish accents.
_ASCII_FOLD = {
    "ç": "c",
    "Ç": "c",
    "ğ": "g",
    "Ğ": "g",
    "ı": "i",
    "İ": "i",
    "ö": "o",
    "Ö": "o",
    "ş": "s",
    "Ş": "s",
    "ü": "u",
    "Ü": "u",
}


@dataclass(frozen=True)
class NormalizedText:
    """Materialised normalization result.

    ``value`` is the lowercase, unicode-normalized primary form the
    matcher uses everywhere. ``ascii_fold`` is the ascii-fold companion
    used only to widen fuzzy matches (never as a replacement).
    """

    original: str
    value: str
    ascii_fold: str
    trigrams: frozenset[str]
    ascii_trigrams: frozenset[str]

    def tokens(self) -> tuple[str, ...]:
        if not self.value:
            return ()
        return tuple(t for t in self.value.split() if t)


def turkish_lower(text: str) -> str:
    """Turkish-aware lowercasing that respects the I/İ/ı/i asymmetry."""

    if not text:
        return ""
    out_chars: list[str] = []
    for ch in text:
        mapped = _TR_LOWER_MAP.get(ch)
        if mapped is not None:
            out_chars.append(mapped)
        else:
            out_chars.append(ch.lower())
    return "".join(out_chars)


def strip_punctuation(text: str) -> str:
    if not text:
        return ""
    return _PUNCT_RE.sub(" ", text)


def collapse_whitespace(text: str) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


def ascii_fold(text: str) -> str:
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        replacement = _ASCII_FOLD.get(ch)
        if replacement is not None:
            out.append(replacement)
            continue
        # Fallback for anything else: NFKD then drop diacritics.
        decomposed = unicodedata.normalize("NFKD", ch)
        stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
        out.append(stripped)
    return "".join(out)


def char_ngrams(text: str, *, n: int = 3) -> frozenset[str]:
    if not text or n <= 0:
        return frozenset()
    padded = f"  {text}  "
    if len(padded) < n:
        return frozenset({padded})
    return frozenset(padded[i : i + n] for i in range(len(padded) - n + 1))


def normalize_turkish(text: str, *, extra: tuple[str, ...] = ()) -> NormalizedText:
    """Full normalization pipeline for a Turkish user query.

    ``extra`` is appended to the primary text before normalization; this
    is how the matcher folds preferences and usage_context hints into
    the query text without the caller having to string-concat.
    """

    parts: list[str] = [text or ""]
    parts.extend(x for x in extra if x)
    joined = " ".join(p for p in parts if p)
    nfkc = unicodedata.normalize("NFKC", joined)
    lowered = turkish_lower(nfkc)
    depunct = strip_punctuation(lowered)
    primary = collapse_whitespace(depunct)
    folded = collapse_whitespace(ascii_fold(primary))
    return NormalizedText(
        original=joined,
        value=primary,
        ascii_fold=folded,
        trigrams=char_ngrams(primary, n=3),
        ascii_trigrams=char_ngrams(folded, n=3),
    )


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return len(inter) / len(a | b)


def trigram_similarity(a: str, b: str) -> float:
    """Similarity between two strings using character trigrams.

    Works on the ascii-folded projection so ``kahve makinesi`` still
    matches ``kahve makınesı`` after Turkish diacritic drift.
    """

    if not a or not b:
        return 0.0
    grams_a = char_ngrams(ascii_fold(turkish_lower(a)), n=3)
    grams_b = char_ngrams(ascii_fold(turkish_lower(b)), n=3)
    return jaccard(grams_a, grams_b)


__all__ = [
    "NormalizedText",
    "ascii_fold",
    "char_ngrams",
    "collapse_whitespace",
    "jaccard",
    "normalize_turkish",
    "strip_punctuation",
    "trigram_similarity",
    "turkish_lower",
]
