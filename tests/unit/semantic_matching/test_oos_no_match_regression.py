"""Regression tests for OOS (out-of-scope) NO_MATCH safety (ADR-007 §F).

Two utterances known to have historically slipped forbidden matches
through the ranker:

* ``uçak bileti almak istiyorum`` (case-hard-nom-03-val-003) — direct OOS
  travel intent; must NEVER auto-select.
* ``uçak bileti mi otel mi`` (case-hard-sib-30-val-030) — sibling
  ambiguity OOS pair; must still resolve to NO_MATCH.

The tests hit both the SemanticCategoryMatcher via the v2 fixture
(which carries ``fixture.out-of-scope-travel`` with matchable=false)
and assert:

* ``result.status is NO_MATCH``
* ``fixture.out-of-scope-travel`` is NEVER in ``result.candidates``
  (final Top-K is matchable-only).
* the decision is NOT ``MATCHED`` under any circumstances.
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


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_V2 = (
    REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v2.json"
)


async def _matcher_and_handle():
    handle = await build_fixture_catalog(fixture_path=FIXTURE_V2)
    matcher = SemanticCategoryMatcher(
        snapshot_provider=handle.service,
        embedding_repository=handle.embedding_repository,
        query_gateway=LexicalFallbackGateway(dim=64),
        policy_provider=StaticSemanticMatchPolicyProvider(SemanticMatchPolicy()),
        cache=InMemoryCategoryMatchCache(),
    )
    return handle, matcher


@pytest.mark.asyncio
async def test_ucak_bileti_almak_istiyorum_never_matches_oos_travel() -> None:
    """case-hard-nom-03-val-003: direct OOS intent → NO_MATCH."""

    handle, matcher = await _matcher_and_handle()
    try:
        result = await matcher.match(
            MatchQuery(
                need_description="uçak bileti almak istiyorum",
                catalog_id=handle.catalog_id,
                locale=handle.locale,
                embedding_profile_id=handle.embedding_profile_id,
                catalog_revision=handle.revision,
            )
        )
    finally:
        await dispose_fixture_catalog(handle)

    assert result.status is not CategoryMatchStatus.MATCHED, (
        f"OOS utterance must NEVER be MATCHED; got {result.status} "
        f"(reason={result.decision.reason_code})"
    )
    assert result.selected_category_id is None
    top_k_slugs = [c.slug for c in result.candidates]
    assert "out-of-scope-travel" not in top_k_slugs, (
        "non-matchable OOS category must not appear in final Top-K"
    )


@pytest.mark.asyncio
async def test_ucak_bileti_mi_otel_mi_never_matches_oos_travel() -> None:
    """case-hard-sib-30-val-030: sibling ambiguity OOS pair → NO_MATCH."""

    handle, matcher = await _matcher_and_handle()
    try:
        result = await matcher.match(
            MatchQuery(
                need_description="uçak bileti mi otel mi",
                catalog_id=handle.catalog_id,
                locale=handle.locale,
                embedding_profile_id=handle.embedding_profile_id,
                catalog_revision=handle.revision,
            )
        )
    finally:
        await dispose_fixture_catalog(handle)

    assert result.status is not CategoryMatchStatus.MATCHED, (
        f"OOS multi-need utterance must NEVER be MATCHED; got {result.status} "
        f"(reason={result.decision.reason_code})"
    )
    top_k_slugs = [c.slug for c in result.candidates]
    assert "out-of-scope-travel" not in top_k_slugs
