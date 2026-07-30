"""ADR-008 P0 — morphology-safe concept normalization tests.

The normalizer must:

* Preserve the user's surface form as the primary concept.
* Emit ``masaüst`` only as a MORPHOLOGICAL_VARIANT of ``masaüstü`` — never
  replace the surface.
* Keep the surface when the user typed a full inflected form like
  ``sistemi`` (only bounded case suffix strips apply).
* Deduplicate variants case-insensitively; empty inputs return empty.
"""

from __future__ import annotations

import pytest

from taksitlio.understanding.normalization.morphology_safe import (
    NormalizationSource,
    TurkishMorphologySafeNormalizer,
    VariantType,
)


@pytest.fixture()
def normalizer() -> TurkishMorphologySafeNormalizer:
    return TurkishMorphologySafeNormalizer()


def _variant_values(normalized) -> list[str]:
    return [v.value for v in normalized.variants]


def _variant_types(normalized) -> list[VariantType]:
    return [v.type for v in normalized.variants]


def test_masaustu_surface_preserved(
    normalizer: TurkishMorphologySafeNormalizer,
) -> None:
    result = normalizer.normalize_concept("masaüstü")
    assert result.surface_form == "masaüstü"
    assert result.primary == "masaüstü"


def test_masaustu_emits_masaust_only_as_variant(
    normalizer: TurkishMorphologySafeNormalizer,
) -> None:
    result = normalizer.normalize_concept("masaüstü")
    values = _variant_values(result)
    types = _variant_types(result)
    # Surface is always the first variant.
    assert values[0] == "masaüstü"
    assert types[0] is VariantType.SURFACE
    # ``masaüst`` must appear ONLY as a morphological alternative.
    morph_values = [
        v.value for v in result.variants if v.type is VariantType.MORPHOLOGICAL_VARIANT
    ]
    assert "masaüst" in morph_values
    # The surface form is never replaced by ``masaüst``.
    assert result.primary != "masaüst"
    # Provenance advertises that a morphological alternative was produced.
    assert result.provenance is NormalizationSource.MORPHOLOGICAL_ALTERNATIVE


def test_sistemi_surface_preserved_when_inflected(
    normalizer: TurkishMorphologySafeNormalizer,
) -> None:
    """``sistemi`` is a valid user surface — normalizer keeps it primary."""

    result = normalizer.normalize_concept("sistemi")
    assert result.surface_form == "sistemi"
    assert result.primary == "sistemi"
    # A morphological variant ``sistem`` MAY appear but the surface must not
    # be replaced.
    if any(v.type is VariantType.MORPHOLOGICAL_VARIANT for v in result.variants):
        morph_values = [
            v.value
            for v in result.variants
            if v.type is VariantType.MORPHOLOGICAL_VARIANT
        ]
        assert result.primary not in morph_values


def test_empty_input_returns_empty_variants(
    normalizer: TurkishMorphologySafeNormalizer,
) -> None:
    result = normalizer.normalize_concept("")
    assert result.surface_form == ""
    assert result.normalized_form == ""
    assert result.variants == ()


def test_variants_deduplicated_case_insensitively(
    normalizer: TurkishMorphologySafeNormalizer,
) -> None:
    """Repeated whitespace / casing must collapse to a single variant."""

    result = normalizer.normalize_concept("  telefon   ")
    keys = [v.value.casefold() for v in result.variants]
    assert len(keys) == len(set(keys))
    assert result.surface_form == "telefon"


def test_all_values_deduplicates_across_surface_normalized_variants(
    normalizer: TurkishMorphologySafeNormalizer,
) -> None:
    result = normalizer.normalize_concept("Telefon")
    values = result.all_values()
    lowered = {v.casefold() for v in values}
    assert len(values) == len(lowered)
    assert values[0] == "Telefon"


def test_to_constraint_fields_exposes_normalization_source(
    normalizer: TurkishMorphologySafeNormalizer,
) -> None:
    payload = normalizer.normalize_concept("masaüstü").to_constraint_fields()
    assert payload["surface_form"] == "masaüstü"
    assert payload["normalization_source"] in {
        NormalizationSource.SURFACE_PRESERVED.value,
        NormalizationSource.MORPHOLOGICAL_ALTERNATIVE.value,
    }
    assert any(v["value"] == "masaüst" for v in payload["variants"])
