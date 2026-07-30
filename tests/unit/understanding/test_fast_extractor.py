"""DeterministicFastExtractor + StubRemoteFastExtractor tests (ADR-007 §3)."""

from __future__ import annotations

import pytest

from taksitlio.semantic_constraints import ConstraintProvenance
from taksitlio.understanding.fast import (
    DeterministicFastExtractor,
    FastDeploymentUnavailable,
    StubRemoteFastExtractor,
)


@pytest.fixture()
def extractor() -> DeterministicFastExtractor:
    return DeterministicFastExtractor()


def _positive_concepts(outcome) -> list[str]:
    return [c.concept for c in outcome.constraints.positive]


def _negative_concepts(outcome) -> list[str]:
    return [c.concept for c in outcome.constraints.negative]


async def test_positive_negative_from_multi_clause_utterance(
    extractor: DeterministicFastExtractor,
) -> None:
    outcome = await extractor.extract("telefon istemiyorum tablet bakıyorum")
    positives = [_normalize(c) for c in _positive_concepts(outcome)]
    negatives = [_normalize(c) for c in _negative_concepts(outcome)]
    assert "tablet" in positives
    assert "telefon" in negatives
    assert not any(_normalize(c) == "telefon" for c in _positive_concepts(outcome))


async def test_correction_pattern_emits_negative_of_old(
    extractor: DeterministicFastExtractor,
) -> None:
    outcome = await extractor.extract("tablet değil laptop lazım")
    positives = [_normalize(c) for c in _positive_concepts(outcome)]
    negatives = [_normalize(c) for c in _negative_concepts(outcome)]
    assert "laptop" in positives
    assert "tablet" in negatives


async def test_correction_yok_boşver_pattern(
    extractor: DeterministicFastExtractor,
) -> None:
    outcome = await extractor.extract("yok telefonu boşver bilgisayar alacağız")
    positives = [_normalize(c) for c in _positive_concepts(outcome)]
    negatives = [_normalize(c) for c in _negative_concepts(outcome)]
    assert any("bilgisayar" in p for p in positives)
    assert any("telefon" in n for n in negatives)


async def test_no_category_ids_or_fixture_keys_emitted(
    extractor: DeterministicFastExtractor,
) -> None:
    outcome = await extractor.extract("telefon istemiyorum tablet bakıyorum")
    for c in outcome.constraints.positive + outcome.constraints.negative:
        assert not c.concept.startswith("fixture."), c
        assert not c.concept.startswith("cat_"), c
        assert not _looks_like_uuid(c.concept), c


async def test_negations_use_explicit_negation_provenance(
    extractor: DeterministicFastExtractor,
) -> None:
    outcome = await extractor.extract("telefon istemiyorum tablet bakıyorum")
    assert all(
        item.provenance is ConstraintProvenance.EXPLICIT_NEGATION
        for item in outcome.constraints.negative
    )


async def test_extractor_name_and_diagnostics(
    extractor: DeterministicFastExtractor,
) -> None:
    outcome = await extractor.extract("telefon istemiyorum tablet bakıyorum")
    assert outcome.extractor == extractor.name
    assert isinstance(outcome.diagnostics, dict)


async def test_negation_with_comma_split_still_isolates_negative_side(
    extractor: DeterministicFastExtractor,
) -> None:
    """Regression: comma-split clauses used to leak the negated noun
    as positive because ``_split_on_neg_cue`` required both sides to be
    non-empty."""

    outcome = await extractor.extract("telefon istemiyorum, bilgisayar arıyorum")
    positives = [_normalize(c) for c in _positive_concepts(outcome)]
    negatives = [_normalize(c) for c in _negative_concepts(outcome)]
    assert "bilgisayar" in positives
    assert "telefon" in negatives
    assert not any(_normalize(c) == "telefon" for c in _positive_concepts(outcome))


async def test_trailing_degil_negates_only_the_last_noun(
    extractor: DeterministicFastExtractor,
) -> None:
    """"X lazım Y değil" — Y is the negated noun, X is the positive.

    Also asserts the "yanlış söyledim" prefix does NOT leak into either
    concept slot.
    """

    outcome = await extractor.extract(
        "yanlış söyledim ses sistemi lazım televizyon değil"
    )
    positives = [_normalize(c) for c in _positive_concepts(outcome)]
    negatives = [_normalize(c) for c in _negative_concepts(outcome)]
    corrections = [
        (_normalize(c.previous_concept), _normalize(c.replacement_concept))
        for c in outcome.constraints.corrections
    ]
    assert any("ses" in p and "sistem" in p for p in positives)
    assert "televizyon" in negatives
    # Correction pair (retracted televizyon → intended ses sistem).
    assert any(prev == "televizyon" and "ses" in repl for prev, repl in corrections)
    # No politeness / verb leakage.
    for c in outcome.constraints.positive + outcome.constraints.negative:
        low = _normalize(c.concept)
        assert "söyledim" not in low
        assert "yanlış" not in low
        assert "dilerim" not in low


async def test_retract_and_replace_pattern_emits_correction(
    extractor: DeterministicFastExtractor,
) -> None:
    """"hayır X demedim Y dedim" — negative X, positive Y, correction pair."""

    outcome = await extractor.extract("hayır telefon demedim tablet dedim")
    positives = [_normalize(c) for c in _positive_concepts(outcome)]
    negatives = [_normalize(c) for c in _negative_concepts(outcome)]
    corrections = [
        (_normalize(c.previous_concept), _normalize(c.replacement_concept))
        for c in outcome.constraints.corrections
    ]
    assert "tablet" in positives
    assert "telefon" in negatives
    assert any(prev == "telefon" and repl == "tablet" for prev, repl in corrections)
    # Never emit "demedim" or "dedim" as a concept.
    for c in outcome.constraints.positive + outcome.constraints.negative:
        low = _normalize(c.concept)
        assert "demedim" not in low
        assert low != "dedim"


async def test_apology_prefix_is_stripped_from_concepts(
    extractor: DeterministicFastExtractor,
) -> None:
    """"özür dilerim X değil Y" — politeness prefix must not leak."""

    outcome = await extractor.extract("özür dilerim tablet değil telefon")
    positives = [_normalize(c) for c in _positive_concepts(outcome)]
    negatives = [_normalize(c) for c in _negative_concepts(outcome)]
    assert "telefon" in positives
    assert "tablet" in negatives
    for c in outcome.constraints.positive + outcome.constraints.negative:
        low = _normalize(c.concept)
        assert "özür" not in low
        assert "dilerim" not in low


async def test_never_emits_stopword_only_concepts(
    extractor: DeterministicFastExtractor,
) -> None:
    """Broad sweep: none of the four ADR-007 §1 regression utterances
    may produce a bare stopword or a `fixture.*` concept.
    """

    hard = [
        "telefon istemiyorum, bilgisayar arıyorum",
        "yanlış söyledim ses sistemi lazım televizyon değil",
        "hayır telefon demedim tablet dedim",
        "özür dilerim tablet değil telefon",
    ]
    forbidden_bare = {"demedim", "dilerim", "söyledim", "yanlış", "özür"}
    for utter in hard:
        outcome = await extractor.extract(utter)
        for c in outcome.constraints.positive + outcome.constraints.negative:
            low = _normalize(c.concept)
            assert low not in forbidden_bare, (utter, c)
            assert not c.concept.startswith("fixture."), (utter, c)
            assert not _looks_like_uuid(c.concept), (utter, c)


async def test_stub_remote_extractor_raises_deployment_unavailable() -> None:
    stub = StubRemoteFastExtractor()
    with pytest.raises(FastDeploymentUnavailable) as excinfo:
        await stub.extract("telefon istemiyorum tablet bakıyorum")
    assert excinfo.value.reason_code == "FAST_DEPLOYMENT_UNAVAILABLE"


def _normalize(text: str) -> str:
    return text.strip().lower()


def _looks_like_uuid(text: str) -> bool:
    import re

    return bool(
        re.match(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            text.strip(),
        )
    )
