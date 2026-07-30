"""Domain models for validated semantic constraints (ADR-007 §4 / ADR-008 P0).

These models are the *validated* runtime shape that the matcher can trust.
They live between the FAST extractor output (natural language concepts) and
the ``MatchQuery.semantic_constraints`` dict consumed by the matcher.

Nothing here references category IDs, catalog UUIDs, fixture keys, or
business word lists. Concepts are natural-language phrases only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from taksitlio.understanding.normalization.morphology_safe import (
    ConceptVariant,
    NormalizationSource,
    VariantType,
)


class ConstraintProvenance(str, Enum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    EXPLICIT_NEGATION = "EXPLICIT_NEGATION"
    USER_CORRECTION = "USER_CORRECTION"
    SESSION_CONTEXT = "SESSION_CONTEXT"


def _parse_variants(raw: Any) -> tuple[ConceptVariant, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    out: list[ConceptVariant] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        value = str(entry.get("value") or "").strip()
        if not value:
            continue
        try:
            vtype = VariantType(str(entry.get("type") or "SURFACE"))
        except ValueError:
            continue
        conf = float(entry.get("confidence", 1.0) or 1.0)
        out.append(ConceptVariant(value=value, type=vtype, confidence=conf))
    return tuple(out)


@dataclass(frozen=True)
class ConstraintItem:
    """One positive / negative constraint concept (morphology-safe)."""

    concept: str
    provenance: ConstraintProvenance
    confidence: float = 0.9
    surface_form: Optional[str] = None
    normalized_form: Optional[str] = None
    variants: tuple[ConceptVariant, ...] = ()
    normalization_source: Optional[NormalizationSource] = None

    @property
    def surface(self) -> str:
        return (self.surface_form or self.concept or "").strip()

    def query_variants(self) -> tuple[str, ...]:
        """All strings the matcher should try (surface first)."""

        values: list[str] = []
        seen: set[str] = set()
        for candidate in (
            self.surface,
            self.normalized_form or "",
            self.concept,
            *(v.value for v in self.variants),
        ):
            key = candidate.casefold().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            values.append(candidate.strip())
        return tuple(values)

    def morphological_variants(self) -> tuple[str, ...]:
        return tuple(
            v.value
            for v in self.variants
            if v.type is VariantType.MORPHOLOGICAL_VARIANT
        )

    def to_matcher_dict(self) -> dict:
        out: dict = {
            "concept": self.concept,
            "provenance": self.provenance.value,
            "confidence": float(self.confidence),
        }
        if self.surface_form:
            out["surface_form"] = self.surface_form
        if self.normalized_form:
            out["normalized_form"] = self.normalized_form
        if self.variants:
            out["variants"] = [v.to_dict() for v in self.variants]
        if self.normalization_source is not None:
            out["normalization_source"] = self.normalization_source.value
        return out

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Optional["ConstraintItem"]:
        concept = str(raw.get("concept") or "").strip()
        if not concept:
            return None
        source = raw.get("provenance") or raw.get("source") or "EXPLICIT"
        try:
            provenance = ConstraintProvenance(str(source))
        except ValueError:
            provenance = ConstraintProvenance.EXPLICIT
        conf = float(raw.get("confidence", raw.get("weight", 0.9)) or 0.9)
        surface = raw.get("surface_form")
        normalized = raw.get("normalized_form")
        variants = _parse_variants(raw.get("variants"))
        norm_src = None
        if raw.get("normalization_source"):
            try:
                norm_src = NormalizationSource(str(raw["normalization_source"]))
            except ValueError:
                norm_src = NormalizationSource.LEGACY_CONCEPT
        elif not surface and not variants:
            norm_src = NormalizationSource.LEGACY_CONCEPT
        return cls(
            concept=concept,
            provenance=provenance,
            confidence=conf,
            surface_form=str(surface).strip() if surface else None,
            normalized_form=str(normalized).strip() if normalized else None,
            variants=variants,
            normalization_source=norm_src,
        )


@dataclass(frozen=True)
class CorrectionItem:
    """A user-correction pair (previous → replacement), surface-preserving."""

    previous_concept: str
    replacement_concept: str
    confidence: float = 0.95
    previous_surface_form: Optional[str] = None
    replacement_surface_form: Optional[str] = None

    def to_matcher_dict(self) -> dict:
        out: dict = {
            "previous_concept": self.previous_concept,
            "replacement_concept": self.replacement_concept,
            "confidence": float(self.confidence),
        }
        if self.previous_surface_form:
            out["previous_surface_form"] = self.previous_surface_form
        if self.replacement_surface_form:
            out["replacement_surface_form"] = self.replacement_surface_form
        return out


@dataclass(frozen=True)
class ValidatedSemanticConstraints:
    """Validated, matcher-ready semantic constraints (ADR-007 / ADR-008)."""

    positive: tuple[ConstraintItem, ...] = ()
    negative: tuple[ConstraintItem, ...] = ()
    corrections: tuple[CorrectionItem, ...] = ()
    rejected_reasons: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.positive or self.negative or self.corrections)

    def to_matcher_dict(self) -> dict:
        """Shape the ``MatchQuery.semantic_constraints`` dict expects."""

        out: dict = {}
        if self.positive:
            out["positive"] = [c.to_matcher_dict() for c in self.positive]
        if self.negative:
            out["negative"] = [c.to_matcher_dict() for c in self.negative]
        if self.corrections:
            out["corrections"] = [c.to_matcher_dict() for c in self.corrections]
        return out


__all__ = [
    "ConstraintItem",
    "ConstraintProvenance",
    "CorrectionItem",
    "ValidatedSemanticConstraints",
]
