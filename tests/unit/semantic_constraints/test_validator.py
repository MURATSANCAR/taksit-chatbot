"""SemanticConstraintValidator unit tests (ADR-007 §4)."""

from __future__ import annotations

import pytest

from taksitlio.semantic_constraints import (
    ConstraintProvenance,
    InvalidConstraintPayload,
    SemanticConstraintValidator,
    SemanticConstraintValidatorConfig,
)


@pytest.fixture()
def validator() -> SemanticConstraintValidator:
    return SemanticConstraintValidator()


def test_none_input_returns_empty_validated(validator: SemanticConstraintValidator) -> None:
    out = validator.validate(None)
    assert out.is_empty
    assert out.rejected_reasons == ()


def test_root_type_must_be_mapping(validator: SemanticConstraintValidator) -> None:
    with pytest.raises(InvalidConstraintPayload):
        validator.validate([])  # type: ignore[arg-type]


def test_uuid_shaped_concept_is_rejected(validator: SemanticConstraintValidator) -> None:
    out = validator.validate(
        {
            "positive": [
                {"concept": "00000000-0000-0000-0000-000000000001", "provenance": "EXPLICIT"},
                {"concept": "tablet", "provenance": "EXPLICIT"},
            ]
        }
    )
    concepts = [item.concept for item in out.positive]
    assert concepts == ["tablet"]
    assert any("uuid_shaped_concept" in reason for reason in out.rejected_reasons)


def test_fixture_key_concept_is_rejected(validator: SemanticConstraintValidator) -> None:
    out = validator.validate(
        {"positive": [{"concept": "fixture.mobile-phones", "provenance": "EXPLICIT"}]}
    )
    assert out.positive == ()
    assert any("fixture_key_leaked_as_concept" in r for r in out.rejected_reasons)


def test_empty_concept_is_rejected(validator: SemanticConstraintValidator) -> None:
    out = validator.validate({"positive": [{"concept": "   ", "provenance": "EXPLICIT"}]})
    assert out.positive == ()
    assert any("empty_concept" in r for r in out.rejected_reasons)


def test_positive_equals_negative_drops_positive(
    validator: SemanticConstraintValidator,
) -> None:
    out = validator.validate(
        {
            "positive": [{"concept": "telefon", "provenance": "EXPLICIT"}],
            "negative": [
                {"concept": "telefon", "provenance": "EXPLICIT_NEGATION", "confidence": 0.9}
            ],
        }
    )
    assert [c.concept for c in out.positive] == []
    assert [c.concept for c in out.negative] == ["telefon"]
    assert any("conflicts_with_explicit_negation" in r for r in out.rejected_reasons)


def test_correction_previous_equals_replacement_is_rejected(
    validator: SemanticConstraintValidator,
) -> None:
    out = validator.validate(
        {
            "corrections": [
                {"previous_concept": "telefon", "replacement_concept": "TELEFON"}
            ]
        }
    )
    assert out.corrections == ()
    assert any("previous_equals_replacement" in r for r in out.rejected_reasons)


def test_duplicates_merged_via_turkish_normalize(
    validator: SemanticConstraintValidator,
) -> None:
    out = validator.validate(
        {
            "positive": [
                {"concept": "İstanbul", "provenance": "EXPLICIT", "confidence": 0.6},
                {"concept": "istanbul", "provenance": "EXPLICIT", "confidence": 0.9},
                {"concept": "İSTANBUL", "provenance": "EXPLICIT", "confidence": 0.7},
            ]
        }
    )
    assert len(out.positive) == 1
    assert out.positive[0].confidence == pytest.approx(0.9)


def test_low_confidence_inferred_dropped_but_explicit_negation_preserved() -> None:
    validator = SemanticConstraintValidator(
        SemanticConstraintValidatorConfig(minimum_inferred_confidence=0.5)
    )
    out = validator.validate(
        {
            "positive": [
                {"concept": "tablet", "provenance": "INFERRED", "confidence": 0.10},
                {"concept": "laptop", "provenance": "INFERRED", "confidence": 0.95},
            ],
            "negative": [
                {"concept": "telefon", "provenance": "EXPLICIT_NEGATION", "confidence": 0.10},
            ],
        }
    )
    positive_concepts = [c.concept for c in out.positive]
    assert positive_concepts == ["laptop"]
    negative_concepts = [c.concept for c in out.negative]
    assert negative_concepts == ["telefon"], (
        "EXPLICIT_NEGATION must be preserved even at low confidence"
    )
    assert any("inferred_below_floor" in r for r in out.rejected_reasons)


def test_slug_like_concept_rejected(validator: SemanticConstraintValidator) -> None:
    out = validator.validate(
        {"positive": [{"concept": "mobile-phones", "provenance": "EXPLICIT"}]}
    )
    assert out.positive == ()
    assert any("slug_like_concept" in r for r in out.rejected_reasons)


def test_legacy_source_key_accepted(validator: SemanticConstraintValidator) -> None:
    out = validator.validate(
        {
            "negative": [{"concept": "telefon", "source": "EXPLICIT_NEGATION"}],
        }
    )
    assert [c.concept for c in out.negative] == ["telefon"]
    assert out.negative[0].provenance is ConstraintProvenance.EXPLICIT_NEGATION


def test_matcher_dict_shape_omits_empty_slots(
    validator: SemanticConstraintValidator,
) -> None:
    out = validator.validate(
        {"positive": [{"concept": "tablet", "provenance": "EXPLICIT"}]}
    )
    payload = out.to_matcher_dict()
    assert "positive" in payload and "negative" not in payload
    assert payload["positive"][0]["concept"] == "tablet"


def test_correction_with_forbidden_previous_rejected(
    validator: SemanticConstraintValidator,
) -> None:
    out = validator.validate(
        {
            "corrections": [
                {
                    "previous_concept": "fixture.mobile-phones",
                    "replacement_concept": "tablet",
                }
            ]
        }
    )
    assert out.corrections == ()
    assert any("fixture_key_leaked_as_concept" in r for r in out.rejected_reasons)
