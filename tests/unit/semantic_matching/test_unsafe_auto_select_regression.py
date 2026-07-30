"""Unsafe auto-select regressions (ADR-007 §9).

These eight utterances historically auto-selected via DIRECT_ALIAS when the
user was *not* buying a product (info / complaint / service / negation).
Unsafe count must stay at 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.evaluation.fixture_catalog import build_fixture_catalog, dispose_fixture_catalog
from taksitlio.semantic_matching import (
    CategoryMatchStatus,
    InMemoryCategoryMatchCache,
    LexicalFallbackGateway,
    MatchQuery,
    SemanticCategoryMatcher,
    SemanticMatchPolicy,
    StaticSemanticMatchPolicyProvider,
)
from taksitlio.semantic_matching.query_intent import (
    QueryIntentKind,
    classify_query_intent,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_V3 = (
    REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v3.json"
)

UNSAFE_UTTERANCES = (
    "tv programına ne dersin",
    "evde tv var yenisi lazım değil",
    "bilgisayar başında çok oturuyorum",
    "araç içi hoparlör kablosu",
    "bisiklet yolları hakkında bilgi",
    "konsol için ip lisansı",
    "mobilya boyama yapılır mı",
    "blender sesinden şikayetçiyim",
)


async def _matcher():
    handle = await build_fixture_catalog(fixture_path=FIXTURE_V3)
    matcher = SemanticCategoryMatcher(
        snapshot_provider=handle.service,
        embedding_repository=handle.embedding_repository,
        query_gateway=LexicalFallbackGateway(dim=64),
        policy_provider=StaticSemanticMatchPolicyProvider(SemanticMatchPolicy()),
        cache=InMemoryCategoryMatchCache(),
    )
    return handle, matcher


@pytest.mark.parametrize("utterance", UNSAFE_UTTERANCES)
def test_unsafe_utterances_classify_as_non_purchase(utterance: str) -> None:
    kind = classify_query_intent(utterance)
    assert kind in {
        QueryIntentKind.NON_PURCHASE,
        QueryIntentKind.OUT_OF_SCOPE,
        QueryIntentKind.CHOICE,
    }, f"{utterance!r} classified as {kind}"


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", UNSAFE_UTTERANCES)
async def test_unsafe_utterances_never_auto_select(utterance: str) -> None:
    handle, matcher = await _matcher()
    try:
        result = await matcher.match(
            MatchQuery(
                need_description=utterance,
                catalog_id=handle.catalog_id,
                locale=handle.locale,
                embedding_profile_id=handle.embedding_profile_id,
                catalog_revision=handle.revision,
            )
        )
    finally:
        await dispose_fixture_catalog(handle)

    assert result.status is not CategoryMatchStatus.MATCHED, (
        f"{utterance!r} must not auto-select; got {result.status} "
        f"({result.decision.reason_code})"
    )
    assert result.selected_category_id is None


@pytest.mark.asyncio
async def test_pad_substring_does_not_match_kapadokya() -> None:
    """Bare substring 'pad' inside 'kapadokya' must not EXACT-alias tablet."""

    handle, matcher = await _matcher()
    try:
        result = await matcher.match(
            MatchQuery(
                need_description="kapadokya turu paketi",
                catalog_id=handle.catalog_id,
                locale=handle.locale,
                embedding_profile_id=handle.embedding_profile_id,
                catalog_revision=handle.revision,
            )
        )
    finally:
        await dispose_fixture_catalog(handle)

    assert result.status is not CategoryMatchStatus.MATCHED
    assert all(c.slug != "tablet-device" or c.signals.alias < 0.8 for c in result.candidates)


@pytest.mark.asyncio
async def test_strong_purchase_direct_alias_still_selects() -> None:
    handle, matcher = await _matcher()
    try:
        result = await matcher.match(
            MatchQuery(
                need_description="tablet almak istiyorum",
                catalog_id=handle.catalog_id,
                locale=handle.locale,
                embedding_profile_id=handle.embedding_profile_id,
                catalog_revision=handle.revision,
            )
        )
    finally:
        await dispose_fixture_catalog(handle)

    assert result.status is CategoryMatchStatus.MATCHED
    assert any(c.slug == "tablet-device" for c in result.candidates[:1])


@pytest.mark.asyncio
async def test_choice_question_stays_ambiguous() -> None:
    handle, matcher = await _matcher()
    try:
        result = await matcher.match(
            MatchQuery(
                need_description="telefonla mı tablet ile mi film izlemeliyim",
                catalog_id=handle.catalog_id,
                locale=handle.locale,
                embedding_profile_id=handle.embedding_profile_id,
                catalog_revision=handle.revision,
            )
        )
    finally:
        await dispose_fixture_catalog(handle)

    assert result.status is CategoryMatchStatus.AMBIGUOUS
    assert result.selected_category_id is None
