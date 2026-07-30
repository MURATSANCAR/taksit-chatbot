"""Dynamic runtime acceptance test for the category matcher.

Scenario (must pass without any Postgres/Redis dependency):

  1. Catalog exists but is empty → matcher returns CATALOG_UNAVAILABLE.
  2. Add a category, localization, alias, use-case, publish → matcher
     resolves the category WITHOUT any process restart.
  3. Archive that category and republish → matcher stops resolving it and
     falls back to NO_MATCH / CATALOG_EMPTY without a restart.

This test integrates the CategoryCatalogService, embedding worker, and the
semantic matcher; each dependency is in-memory but wired the same way the
production stack will wire them.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from taksitlio.category_catalog import (
    CategoryCatalogService,
    InMemoryCategoryCatalogRepository,
    MatchMode,
)
from taksitlio.category_embedding import (
    CategoryEmbeddingOutbox,
    CategoryEmbeddingWorker,
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.embeddings.client import LexicalEmbedder
from taksitlio.semantic_matching import (
    CategoryMatchStatus,
    CategoryResolutionApplier,
    InMemoryCategoryMatchCache,
    LexicalFallbackGateway,
    MatchQuery,
    SemanticCategoryMatcher,
    SemanticMatchPolicy,
    StaticSemanticMatchPolicyProvider,
)
from taksitlio.conversation_state import (
    ConversationStateManager,
    InMemoryConversationStateRepository,
)


class _EmbClient:
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await LexicalEmbedder(dim=64).embed(list(texts))



@pytest.mark.asyncio
async def test_dynamic_runtime_category_end_to_end():
    catalog_repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(catalog_repo)
    catalog = await service.create_catalog(
        catalog_code="RUNTIME", display_name="Runtime katalogu"
    )
    embedding_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embedding_repo)
    worker = CategoryEmbeddingWorker(embedding_repo, _EmbClient())
    policy = SemanticMatchPolicy(
        minimum_candidate_score=0.1,
        minimum_auto_select_score=0.1,
        cache_ttl_seconds=0,
    )
    matcher = SemanticCategoryMatcher(
        snapshot_provider=service,
        embedding_repository=embedding_repo,
        query_gateway=LexicalFallbackGateway(dim=64),
        policy_provider=StaticSemanticMatchPolicyProvider(policy),
        cache=InMemoryCategoryMatchCache(),
    )

    # --- Step 1: catalog empty → CATALOG_UNAVAILABLE
    result = await matcher.match(
        MatchQuery(
            text="telefon almak istiyorum",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=0,
        )
    )
    assert result.status == CategoryMatchStatus.CATALOG_UNAVAILABLE

    # --- Step 2: add a category dynamically and publish
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="mobile-device",
        semantic_description="Cep telefonu ve mobil cihaz talepleri",
    )
    await service.add_localization(
        category_id=category.id,
        locale=catalog.primary_locale,
        display_name="Mobil cihaz",
        description="Cep telefonu ve akıllı telefon ihtiyaçları",
        synonyms=("telefon", "mobil"),
    )
    await service.add_alias(
        category_id=category.id,
        locale=catalog.primary_locale,
        alias_text="telefon",
        alias_type=MatchMode.EXACT,
    )
    await service.add_use_case(
        category_id=category.id,
        locale=catalog.primary_locale,
        use_case_text="Kamera odaklı telefon alma",
    )
    from taksitlio.category_catalog.publish_pipeline import prepare_embed_and_publish
    await prepare_embed_and_publish(
        service, catalog.id, outbox, worker, embedding_repo=embedding_repo
    )

    # matcher resolves without any restart
    result = await matcher.match(
        MatchQuery(
            text="kamera kalitesi iyi bir telefon arıyorum",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
        )
    )
    assert result.status == CategoryMatchStatus.MATCHED
    assert result.selected_category_id == category.id

    # --- Bridge: matcher pipes into the conversation state manager only.
    manager = ConversationStateManager(InMemoryConversationStateRepository())
    session = await manager.create_session()
    applier = CategoryResolutionApplier(manager)
    outcome = await applier.apply(
        session_id=session.session_id,
        expected_revision=0,
        match_result=result,
        idempotency_key="cat-1",
        client_message_id="msg-1",
        client_sequence=1,
    )
    assert outcome.applied
    refreshed = await manager.get_session(session.session_id)
    assert refreshed.active_need is not None
    resolution = refreshed.active_need.category_resolution
    assert resolution.selected_category_id == category.id
    assert resolution.match_status == CategoryMatchStatus.MATCHED.value
    assert resolution.catalog_id == catalog.id
    assert resolution.catalog_revision == 1
    # No embeddings or aliases leaked into the state
    payload = refreshed.active_need.to_dict()
    assert "aliases" not in payload["category_resolution"]
    assert "embedding" not in payload["category_resolution"]

    # --- Step 3: archive category, republish, matcher stops resolving it
    await service.archive_category(category.id)
    from taksitlio.category_catalog.publish_pipeline import prepare_embed_and_publish
    await prepare_embed_and_publish(
        service, catalog.id, outbox, worker, embedding_repo=embedding_repo
    )
    result_after_archive = await matcher.match(
        MatchQuery(
            text="kamera kalitesi iyi bir telefon arıyorum",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=2,
        )
    )
    assert result_after_archive.status in {
        CategoryMatchStatus.CATALOG_EMPTY,
        CategoryMatchStatus.NO_MATCH,
    }
    assert result_after_archive.selected_category_id is None
