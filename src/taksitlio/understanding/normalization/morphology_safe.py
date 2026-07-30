"""Morphology-safe Turkish concept normalization (ADR-008 P0).

Surface form is always primary. Aggressive stripping produces *variants*
only — never replaces the surface. No category-specific word lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from taksitlio.semantic_matching.turkish_normalize import (
    ascii_fold,
    normalize_turkish,
    turkish_lower,
)


class VariantType(str, Enum):
    SURFACE = "SURFACE"
    NORMALIZED = "NORMALIZED"
    MORPHOLOGICAL_VARIANT = "MORPHOLOGICAL_VARIANT"
    CHARACTERLESS_TURKISH = "CHARACTERLESS_TURKISH"
    TOKEN_SET = "TOKEN_SET"


class NormalizationSource(str, Enum):
    SURFACE_PRESERVED = "SURFACE_PRESERVED"
    SAFE_NORMALIZATION = "SAFE_NORMALIZATION"
    MORPHOLOGICAL_ALTERNATIVE = "MORPHOLOGICAL_ALTERNATIVE"
    CHARACTER_NORMALIZATION = "CHARACTER_NORMALIZATION"
    LEGACY_CONCEPT = "LEGACY_CONCEPT"


@dataclass(frozen=True)
class ConceptVariant:
    value: str
    type: VariantType
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "type": self.type.value,
            "confidence": float(self.confidence),
        }


@dataclass(frozen=True)
class NormalizedConcept:
    surface_form: str
    normalized_form: str
    variants: tuple[ConceptVariant, ...]
    provenance: NormalizationSource

    @property
    def primary(self) -> str:
        """Canonical concept string for matcher / schema ``concept`` field."""

        return self.surface_form or self.normalized_form

    def all_values(self) -> tuple[str, ...]:
        seen: list[str] = []
        for value in (self.surface_form, self.normalized_form) + tuple(
            v.value for v in self.variants
        ):
            key = turkish_lower(value)
            if value and key not in {turkish_lower(s) for s in seen}:
                seen.append(value)
        return tuple(seen)

    def to_constraint_fields(self) -> dict:
        return {
            "concept": self.primary,
            "surface_form": self.surface_form,
            "normalized_form": self.normalized_form,
            "variants": [v.to_dict() for v in self.variants],
            "normalization_source": self.provenance.value,
        }


# Content-blind: vowel set for controlled suffix heuristics only.
_VOWELS = set("aeıioöuüAEIİOÖUÜ")
_MIN_MORPH_LEN = 4


def _safe_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def _controlled_suffix_strip(token: str) -> Optional[str]:
    """Produce a morphology *alternative* without replacing the surface.

    Only strips a single trailing case/possessive vowel after a consonant
    on tokens long enough to remain meaningful. Never used as primary.
    """

    tok = token.strip()
    if len(tok) < _MIN_MORPH_LEN + 1:
        return None
    last = tok[-1]
    if last not in "uüıi" or tok[-2] in _VOWELS:
        return None
    stripped = tok[:-1]
    if len(stripped) < _MIN_MORPH_LEN:
        return None
    if stripped == tok:
        return None
    return stripped


class TurkishMorphologySafeNormalizer:
    """Normalize a concept while preserving the user surface form."""

    def __init__(self, *, morphological_variant_min_length: int = 4) -> None:
        self._min_morph = max(2, int(morphological_variant_min_length))

    def normalize_concept(self, text: str) -> NormalizedConcept:
        surface = _safe_whitespace(text)
        if not surface:
            return NormalizedConcept(
                surface_form="",
                normalized_form="",
                variants=(),
                provenance=NormalizationSource.SURFACE_PRESERVED,
            )

        norm = normalize_turkish(surface)
        normalized_form = norm.value or turkish_lower(surface)
        variants: list[ConceptVariant] = [
            ConceptVariant(surface, VariantType.SURFACE, 1.0),
        ]
        if normalized_form and turkish_lower(normalized_form) != turkish_lower(surface):
            variants.append(
                ConceptVariant(normalized_form, VariantType.NORMALIZED, 0.95)
            )

        # Characterless companion (ascii-fold) — never primary.
        folded = ascii_fold(normalized_form or surface)
        if folded and turkish_lower(folded) not in {
            turkish_lower(surface),
            turkish_lower(normalized_form),
        }:
            variants.append(
                ConceptVariant(folded, VariantType.CHARACTERLESS_TURKISH, 0.7)
            )

        # Morphological alternatives per token — variants only.
        morph_seen: set[str] = set()
        for token in surface.split():
            alt = _controlled_suffix_strip(token)
            if not alt or len(alt) < self._min_morph:
                continue
            key = turkish_lower(alt)
            if key in morph_seen or key == turkish_lower(token):
                continue
            morph_seen.add(key)
            # Rebuild phrase replacing this token with alt when multi-token.
            if " " in surface:
                parts = [
                    alt if turkish_lower(p) == turkish_lower(token) else p
                    for p in surface.split()
                ]
                phrase = " ".join(parts)
            else:
                phrase = alt
            if turkish_lower(phrase) in {
                turkish_lower(surface),
                turkish_lower(normalized_form),
            }:
                continue
            variants.append(
                ConceptVariant(phrase, VariantType.MORPHOLOGICAL_VARIANT, 0.65)
            )

        # Deduplicate variants by lowercased value, keep first.
        dedup: list[ConceptVariant] = []
        seen_keys: set[str] = set()
        for variant in variants:
            key = turkish_lower(variant.value)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            dedup.append(variant)

        provenance = NormalizationSource.SURFACE_PRESERVED
        if any(v.type is VariantType.MORPHOLOGICAL_VARIANT for v in dedup):
            provenance = NormalizationSource.MORPHOLOGICAL_ALTERNATIVE

        return NormalizedConcept(
            surface_form=surface,
            normalized_form=normalized_form or surface,
            variants=tuple(dedup),
            provenance=provenance,
        )


def pick_surface_head_noun(
    span: str,
    *,
    stopwords: Iterable[str],
    head_only: bool = False,
) -> Optional[str]:
    """Extract a surface head noun *without* aggressive stripping.

    Stopwords are content-blind (verbs, particles). The returned string is
    the surface tokens as spoken — morphology variants are produced later
    by ``TurkishMorphologySafeNormalizer``.
    """

    if not span:
        return None
    stop = {ascii_fold(turkish_lower(w)) for w in stopwords}
    tokens = tuple(
        m
        for m in __import__("re").findall(
            r"[\wçğıöşüÇĞİÖŞÜ']+", span or "", flags=__import__("re").UNICODE
        )
    )
    keep = tuple(
        t for t in tokens if ascii_fold(turkish_lower(t)) not in stop
    )
    if not keep:
        return None
    if head_only or len(keep) < 2:
        concept = keep[-1]
    else:
        prev = keep[-2]
        if len(prev) <= 10 and prev.isalpha():
            concept = f"{prev} {keep[-1]}"
        else:
            concept = keep[-1]
    concept = concept.strip()
    return concept if len(concept) >= 2 else None


__all__ = [
    "ConceptVariant",
    "NormalizedConcept",
    "NormalizationSource",
    "TurkishMorphologySafeNormalizer",
    "VariantType",
    "pick_surface_head_noun",
]
