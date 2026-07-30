"""SemanticConstraintValidator — ADR-007 §4.

The validator sits between whatever produced a NeedProfile (FAST model,
runtime rule extractor, or annotated evaluation case) and the matcher.
It guarantees the matcher never sees:

* UUID-shaped strings masquerading as concepts;
* ``fixture.*`` catalog keys, category codes, or slug-shaped strings;
* empty / whitespace concepts;
* identical positive AND negative concepts;
* corrections where ``previous_concept == replacement_concept``;
* low-confidence INFERRED constraints below the configured floor;
* duplicate concepts differing only by Turkish diacritics / casing.

Explicit-negation / user-correction constraints are always preserved
(they trump the low-confidence filter — the whole safety story of
ADR-006/ADR-007 depends on them reaching the matcher).

The validator is intentionally *content-blind*: it does not know any
business word list, category alias table, or catalog identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from taksitlio.semantic_constraints.domain import (
    ConstraintItem,
    ConstraintProvenance,
    CorrectionItem,
    ValidatedSemanticConstraints,
)
from taksitlio.semantic_constraints.errors import (
    ConstraintRejected,
    InvalidConstraintPayload,
)
from taksitlio.semantic_matching.turkish_normalize import (
    ascii_fold,
    turkish_lower,
)
from taksitlio.understanding.normalization.morphology_safe import (
    ConceptVariant,
    NormalizationSource,
    VariantType,
)


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
FIXTURE_KEY_RE = re.compile(r"^fixture\.[a-z0-9-]+$", re.IGNORECASE)
# A "slug-like" shape: only ascii lowercase letters, digits and hyphens with at
# least one hyphen and no whitespace. We treat these as catalog identifiers
# that must NOT be smuggled in as a natural-language concept.
SLUG_LIKE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){1,}$")


@dataclass(frozen=True)
class SemanticConstraintValidatorConfig:
    minimum_inferred_confidence: float = 0.45
    minimum_explicit_confidence: float = 0.15
    max_positive: int = 16
    max_negative: int = 16
    max_corrections: int = 8
    max_concept_length: int = 128


def _normalize_concept(text: str) -> str:
    """Turkish-aware, ascii-fold normalization for de-duplication only."""

    if not text:
        return ""
    lowered = turkish_lower(text.strip())
    folded = ascii_fold(lowered)
    return " ".join(folded.split())


def _is_forbidden_concept_shape(concept: str) -> Optional[str]:
    """Return a reason string if ``concept`` looks like an identifier."""

    stripped = concept.strip()
    if not stripped:
        return "empty_concept"
    if UUID_RE.match(stripped):
        return "uuid_shaped_concept"
    if FIXTURE_KEY_RE.match(stripped):
        return "fixture_key_leaked_as_concept"
    if SLUG_LIKE_RE.match(stripped) and " " not in stripped:
        # Slug shapes like "portable-computer" or "mobile-device". Natural
        # user phrases contain whitespace or are single tokens without
        # multiple hyphen-joined lowercase parts.
        return "slug_like_concept"
    return None


class SemanticConstraintValidator:
    """Validate + normalize raw constraint payloads.

    The validator accepts the shape the FAST/NeedProfile schema produces
    (``constraint_item`` with ``concept`` / ``provenance`` / optional
    ``confidence``) as well as the legacy matcher shape (``source`` alias
    for ``provenance``, ``previous_concept`` / ``replacement_concept``
    pairs for corrections).
    """

    def __init__(
        self,
        config: Optional[SemanticConstraintValidatorConfig] = None,
    ) -> None:
        self._config = config or SemanticConstraintValidatorConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        raw: Optional[Mapping[str, Any]],
    ) -> ValidatedSemanticConstraints:
        if raw is None:
            return ValidatedSemanticConstraints()
        if not isinstance(raw, Mapping):
            raise InvalidConstraintPayload(
                "semantic_constraints must be a mapping",
                reason_code="INVALID_ROOT_TYPE",
            )
        rejected: list[str] = []
        positive = self._validate_list(
            raw.get("positive") or (),
            allow_empty_provenance=ConstraintProvenance.EXPLICIT,
            rejected=rejected,
            slot="positive",
        )
        negative = self._validate_list(
            raw.get("negative") or (),
            allow_empty_provenance=ConstraintProvenance.EXPLICIT_NEGATION,
            rejected=rejected,
            slot="negative",
        )
        corrections = self._validate_corrections(
            raw.get("corrections") or (), rejected=rejected
        )

        # Cross-list checks (positive == negative).
        positive, negative, cross_rejected = self._cross_check(
            positive, negative
        )
        rejected.extend(cross_rejected)

        # Merge duplicates via Turkish normalize.
        positive = self._merge_duplicates(positive)
        negative = self._merge_duplicates(negative)

        # Apply confidence floors AFTER merging so a merged pair with the
        # best confidence survives.
        positive = self._apply_confidence_floor(
            positive, rejected=rejected, slot="positive"
        )
        negative = self._apply_confidence_floor(
            negative, rejected=rejected, slot="negative"
        )

        # Enforce absolute caps.
        positive = positive[: self._config.max_positive]
        negative = negative[: self._config.max_negative]
        corrections = corrections[: self._config.max_corrections]

        return ValidatedSemanticConstraints(
            positive=tuple(positive),
            negative=tuple(negative),
            corrections=tuple(corrections),
            rejected_reasons=tuple(rejected),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_list(
        self,
        entries: Iterable[Any],
        *,
        allow_empty_provenance: ConstraintProvenance,
        rejected: list[str],
        slot: str,
    ) -> list[ConstraintItem]:
        out: list[ConstraintItem] = []
        for idx, entry in enumerate(entries):
            item = self._parse_constraint_item(
                entry,
                allow_empty_provenance=allow_empty_provenance,
                rejected=rejected,
                slot=slot,
                idx=idx,
            )
            if item is not None:
                out.append(item)
        return out

    def _parse_constraint_item(
        self,
        entry: Any,
        *,
        allow_empty_provenance: ConstraintProvenance,
        rejected: list[str],
        slot: str,
        idx: int,
    ) -> Optional[ConstraintItem]:
        if not isinstance(entry, Mapping):
            rejected.append(f"{slot}[{idx}]:not_mapping")
            return None
        concept_raw = entry.get("concept")
        if not isinstance(concept_raw, str):
            rejected.append(f"{slot}[{idx}]:concept_not_string")
            return None
        concept = concept_raw.strip()
        forbidden = _is_forbidden_concept_shape(concept)
        if forbidden is not None:
            rejected.append(f"{slot}[{idx}]:{forbidden}")
            return None
        if len(concept) > self._config.max_concept_length:
            rejected.append(f"{slot}[{idx}]:concept_too_long")
            return None
        # Accept either "provenance" or the legacy "source" key.
        prov_raw = entry.get("provenance") or entry.get("source")
        if not prov_raw:
            provenance = allow_empty_provenance
        else:
            try:
                provenance = ConstraintProvenance(str(prov_raw))
            except ValueError:
                rejected.append(f"{slot}[{idx}]:unknown_provenance:{prov_raw}")
                return None
        confidence_raw = entry.get("confidence", 0.9)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            rejected.append(f"{slot}[{idx}]:confidence_not_number")
            return None
        if not 0.0 <= confidence <= 1.0:
            rejected.append(f"{slot}[{idx}]:confidence_out_of_range")
            return None
        return ConstraintItem(
            concept=concept,
            provenance=provenance,
            confidence=confidence,
            surface_form=(
                str(entry["surface_form"]).strip()
                if entry.get("surface_form")
                else None
            ),
            normalized_form=(
                str(entry["normalized_form"]).strip()
                if entry.get("normalized_form")
                else None
            ),
            variants=_parse_variants_safe(entry.get("variants")),
            normalization_source=_parse_norm_source(entry.get("normalization_source")),
        )

    def _validate_corrections(
        self,
        entries: Iterable[Any],
        *,
        rejected: list[str],
    ) -> list[CorrectionItem]:
        out: list[CorrectionItem] = []
        for idx, entry in enumerate(entries):
            item = self._parse_correction_item(entry, rejected=rejected, idx=idx)
            if item is not None:
                out.append(item)
        return out

    def _parse_correction_item(
        self,
        entry: Any,
        *,
        rejected: list[str],
        idx: int,
    ) -> Optional[CorrectionItem]:
        if not isinstance(entry, Mapping):
            rejected.append(f"corrections[{idx}]:not_mapping")
            return None

        # Two accepted shapes:
        #  1. Explicit pair: {"previous_concept": ..., "replacement_concept": ...}
        #  2. Legacy single: {"concept": ..., "provenance"/"source": USER_CORRECTION}
        prev_raw = entry.get("previous_concept")
        repl_raw = entry.get("replacement_concept")
        if not (isinstance(prev_raw, str) and isinstance(repl_raw, str)):
            # Try legacy single-form.
            concept = entry.get("concept")
            provenance = entry.get("provenance") or entry.get("source")
            if isinstance(concept, str) and provenance in {
                "USER_CORRECTION",
                ConstraintProvenance.USER_CORRECTION.value,
            }:
                # A single-concept correction (previous only) will be
                # emitted as a *negative* by the caller. We still return
                # None here so the loop skips it — corrections require a
                # full previous → replacement pair.
                rejected.append(
                    f"corrections[{idx}]:missing_replacement_use_negative_slot_instead"
                )
                return None
            rejected.append(f"corrections[{idx}]:missing_previous_or_replacement")
            return None

        prev = prev_raw.strip()
        repl = repl_raw.strip()
        for label, value in (("previous", prev), ("replacement", repl)):
            forbidden = _is_forbidden_concept_shape(value)
            if forbidden is not None:
                rejected.append(f"corrections[{idx}]:{label}:{forbidden}")
                return None
        if _normalize_concept(prev) == _normalize_concept(repl):
            rejected.append(f"corrections[{idx}]:previous_equals_replacement")
            return None
        try:
            confidence = float(entry.get("confidence", 0.95))
        except (TypeError, ValueError):
            rejected.append(f"corrections[{idx}]:confidence_not_number")
            return None
        if not 0.0 <= confidence <= 1.0:
            rejected.append(f"corrections[{idx}]:confidence_out_of_range")
            return None
        return CorrectionItem(
            previous_concept=prev,
            replacement_concept=repl,
            confidence=confidence,
            previous_surface_form=(
                str(entry["previous_surface_form"]).strip()
                if entry.get("previous_surface_form")
                else None
            ),
            replacement_surface_form=(
                str(entry["replacement_surface_form"]).strip()
                if entry.get("replacement_surface_form")
                else None
            ),
        )

    def _cross_check(
        self,
        positive: list[ConstraintItem],
        negative: list[ConstraintItem],
    ) -> tuple[list[ConstraintItem], list[ConstraintItem], list[str]]:
        rejected: list[str] = []
        pos_by_norm = {_normalize_concept(c.concept): c for c in positive}
        conflicting_neg: set[str] = set()
        for neg in negative:
            key = _normalize_concept(neg.concept)
            if key in pos_by_norm:
                # Explicit negation wins over a bare positive of the same
                # phrase — otherwise "telefon istemiyorum, telefon lazım"
                # both survive and confuse the matcher. Drop the positive.
                rejected.append(
                    f"positive:{neg.concept}:conflicts_with_explicit_negation"
                )
                conflicting_neg.add(key)
        positive_kept = [
            p for p in positive if _normalize_concept(p.concept) not in conflicting_neg
        ]
        return positive_kept, negative, rejected

    def _merge_duplicates(
        self,
        items: list[ConstraintItem],
    ) -> list[ConstraintItem]:
        """Collapse duplicates via Turkish normalize, keeping highest confidence.

        Preserves the provenance of the surviving entry and prefers
        EXPLICIT_NEGATION / USER_CORRECTION over INFERRED.
        """

        by_key: dict[str, ConstraintItem] = {}
        priority = {
            ConstraintProvenance.EXPLICIT_NEGATION: 4,
            ConstraintProvenance.USER_CORRECTION: 4,
            ConstraintProvenance.EXPLICIT: 3,
            ConstraintProvenance.SESSION_CONTEXT: 2,
            ConstraintProvenance.INFERRED: 1,
        }
        for item in items:
            key = _normalize_concept(item.concept)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = item
                continue
            if priority[item.provenance] > priority[existing.provenance]:
                by_key[key] = item
                continue
            if priority[item.provenance] == priority[existing.provenance]:
                if item.confidence > existing.confidence:
                    by_key[key] = item
        return list(by_key.values())

    def _apply_confidence_floor(
        self,
        items: list[ConstraintItem],
        *,
        rejected: list[str],
        slot: str,
    ) -> list[ConstraintItem]:
        cfg = self._config
        out: list[ConstraintItem] = []
        for item in items:
            # EXPLICIT_NEGATION / USER_CORRECTION are always preserved —
            # they carry the safety guarantee (ADR-006/ADR-007).
            if item.provenance in {
                ConstraintProvenance.EXPLICIT_NEGATION,
                ConstraintProvenance.USER_CORRECTION,
            }:
                out.append(item)
                continue
            if item.provenance is ConstraintProvenance.INFERRED:
                if item.confidence < cfg.minimum_inferred_confidence:
                    rejected.append(
                        f"{slot}:{item.concept}:inferred_below_floor"
                    )
                    continue
            elif item.confidence < cfg.minimum_explicit_confidence:
                rejected.append(f"{slot}:{item.concept}:below_confidence_floor")
                continue
            out.append(item)
        return out


def _parse_variants_safe(raw: Any) -> tuple[ConceptVariant, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[ConceptVariant] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        value = str(entry.get("value") or "").strip()
        if not value:
            continue
        key = _normalize_concept(value)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            vtype = VariantType(str(entry.get("type") or "SURFACE"))
        except ValueError:
            continue
        try:
            conf = float(entry.get("confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            conf = 1.0
        out.append(ConceptVariant(value=value, type=vtype, confidence=conf))
    return tuple(out)


def _parse_norm_source(raw: Any) -> Optional[NormalizationSource]:
    if not raw:
        return None
    try:
        return NormalizationSource(str(raw))
    except ValueError:
        return NormalizationSource.LEGACY_CONCEPT


__all__ = [
    "SemanticConstraintValidator",
    "SemanticConstraintValidatorConfig",
]
