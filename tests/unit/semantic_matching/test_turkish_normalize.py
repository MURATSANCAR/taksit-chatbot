"""Unit tests for Turkish-aware text normalization (ADR-006 §E)."""

from __future__ import annotations

import pytest

from taksitlio.semantic_matching.turkish_normalize import (
    ascii_fold,
    char_ngrams,
    normalize_turkish,
    trigram_similarity,
    turkish_lower,
)


def test_turkish_lower_respects_dotted_i() -> None:
    assert turkish_lower("İZMİR") == "izmir"
    assert turkish_lower("Iyi") == "ıyi"
    assert turkish_lower("iyi") == "iyi"
    assert turkish_lower("ISPARTA") == "ısparta"


def test_ascii_fold_maps_common_turkish_chars() -> None:
    assert ascii_fold("şöförüm çığlık") == "soforum ciglik"


def test_normalize_turkish_pipeline_strips_punctuation_and_lowercases() -> None:
    n = normalize_turkish("İzmir'de, Kahve Makinesi!!!")
    assert n.value == "izmir de kahve makinesi"
    assert n.ascii_fold == "izmir de kahve makinesi"


def test_normalize_turkish_appends_extra_hints() -> None:
    n = normalize_turkish("kahve", extra=("makinesi", "gümüş", ""))
    assert "kahve" in n.value
    assert "makinesi" in n.value
    assert "gumus" in n.ascii_fold


def test_char_ngrams_produces_padded_trigrams() -> None:
    trigrams = char_ngrams("abc", n=3)
    assert "  a" in trigrams or " a" in trigrams or "abc" in trigrams


def test_trigram_similarity_survives_diacritic_drift() -> None:
    """`kahve makınesı` (ASCII soft) ≈ `kahve makinesi` after ascii-fold."""

    score = trigram_similarity("kahve makinesi", "kahve makınesı")
    assert score > 0.9


def test_normalize_turkish_no_content_specific_word_lists() -> None:
    """Confidence check: no category slug leaks into the normalizer output.

    The normalizer is content-blind — it just processes text. This
    guardrail keeps the linguistic module free of business vocabulary.
    """

    n = normalize_turkish("laptop veya masaüstü")
    assert n.value == "laptop veya masaüstü"
    assert n.ascii_fold == "laptop veya masaustu"
