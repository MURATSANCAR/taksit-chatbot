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
