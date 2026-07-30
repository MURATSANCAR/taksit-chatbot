"""Unit tests for the dynamic semantic category matcher."""

from __future__ import annotations

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
    AlwaysFailingGateway,
    CategoryMatchStatus,
    InMemoryCategoryMatchCache,
    LexicalFallbackGateway,
    MatchQuery,
    SemanticCategoryMatcher,
    SemanticMatchPolicy,
    StaticSemanticMatchPolicyProvider,
)
from taksitlio.semantic_matching.cache import build_cache_key


class _EmbClient:
    async def embed(self, texts):
        return await LexicalEmbedder(dim=64).embed(list(texts))


async def _build_stack(*, policy: SemanticMatchPolicy | None = None):
    catalog_repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(catalog_repo)
    catalog = await service.create_catalog(
        catalog_code="MATCH", display_name="MATCH"
    )
    embedding_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embedding_repo)
    worker = CategoryEmbeddingWorker(embedding_repo, _EmbClient())
    matcher = SemanticCategoryMatcher(
        snapshot_provider=service,
        embedding_repository=embedding_repo,
        query_gateway=LexicalFallbackGateway(dim=64),
        policy_provider=StaticSemanticMatchPolicyProvider(
            policy or SemanticMatchPolicy()
        ),
        cache=InMemoryCategoryMatchCache(),
    )
    return service, catalog, embedding_repo, outbox, worker, matcher


async def _publish_and_embed(service, catalog, outbox, worker) -> None:
    snapshot = await service.get_published_snapshot(catalog.id)
    if snapshot is None:
        return
    await outbox.enqueue_for_snapshot(snapshot, embedding_profile_id="p1")
    await worker.run_once()


@pytest.mark.asyncio
async def test_catalog_empty_status_when_no_publish():
    _, catalog, _, _, _, matcher = await _build_stack()
    result = await matcher.match(
        MatchQuery(
            text="rastgele metin",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=0,
        )
    )
    assert result.status == CategoryMatchStatus.CATALOG_UNAVAILABLE


@pytest.mark.asyncio
async def test_match_returns_matched_status_after_publish_and_embed():
    service, catalog, embedding_repo, outbox, worker, matcher = await _build_stack()
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="mobile",
        semantic_description="Mobil ürün, akıllı telefon, cep telefonu ihtiyaçları",
    )
    await service.add_localization(
        category_id=category.id,
        locale=catalog.primary_locale,
        display_name="Mobil",
        description="Mobil ürünler",
        synonyms=("telefon", "mobil"),
    )
    await service.add_alias(
        category_id=category.id,
        locale=catalog.primary_locale,
        alias_text="telefon",
    )
    await service.add_use_case(
        category_id=category.id,
        locale=catalog.primary_locale,
        use_case_text="Mobil ürün alma",
    )
    await service.publish_revision(catalog.id)
    await _publish_and_embed(service, catalog, outbox, worker)
    result = await matcher.match(
        MatchQuery(
            text="Kamera kalitesi iyi bir telefon arıyorum",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
        )
    )
    assert result.status == CategoryMatchStatus.MATCHED
    assert result.selected_category_id == category.id
    assert result.candidates
    assert result.candidates[0].signals.alias_text == "telefon"


@pytest.mark.asyncio
async def test_ambiguous_detected_when_gap_within_threshold():
    policy = SemanticMatchPolicy(
        clarify_score_gap=0.35, minimum_score=0.1, maximum_candidates=3
    )
    service, catalog, embedding_repo, outbox, worker, matcher = await _build_stack(
        policy=policy
    )
    for slug, alias in (("mobile", "telefon"), ("laptop", "bilgisayar")):
        category = await service.add_category(
            catalog_id=catalog.id,
            slug=slug,
            semantic_description=f"{slug} ihtiyaçları için genel açıklama",
        )
        await service.add_localization(
            category_id=category.id,
            locale=catalog.primary_locale,
            display_name=slug.title(),
            synonyms=(alias,),
        )
        await service.add_alias(
            category_id=category.id,
            locale=catalog.primary_locale,
            alias_text=alias,
        )
    await service.publish_revision(catalog.id)
    await _publish_and_embed(service, catalog, outbox, worker)
    result = await matcher.match(
        MatchQuery(
            text="telefon bilgisayar ikisi de olur",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
        )
    )
    assert result.status == CategoryMatchStatus.AMBIGUOUS
    assert result.decision.selected_category_id is None


@pytest.mark.asyncio
async def test_degraded_mode_when_embedding_gateway_unavailable():
    service, catalog, embedding_repo, outbox, worker, _ = await _build_stack()
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="mobile",
        semantic_description="Mobile devices",
    )
    await service.add_localization(
        category_id=category.id,
        locale=catalog.primary_locale,
        display_name="Mobil",
        synonyms=("telefon",),
    )
    await service.add_alias(
        category_id=category.id,
        locale=catalog.primary_locale,
        alias_text="telefon",
    )
    await service.publish_revision(catalog.id)
    await _publish_and_embed(service, catalog, outbox, worker)

    degraded_policy = SemanticMatchPolicy(minimum_score=0.3)
    matcher = SemanticCategoryMatcher(
        snapshot_provider=service,
        embedding_repository=embedding_repo,
        query_gateway=AlwaysFailingGateway(),
        policy_provider=StaticSemanticMatchPolicyProvider(degraded_policy),
        cache=InMemoryCategoryMatchCache(),
    )
    result = await matcher.match(
        MatchQuery(
            text="telefon almak istiyorum",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
        )
    )
    assert result.degraded is True
    assert result.status == CategoryMatchStatus.MATCHED
    assert result.selected_category_id == category.id


@pytest.mark.asyncio
async def test_cache_key_never_contains_raw_text():
    policy = SemanticMatchPolicy(policy_version=7)
    query = MatchQuery(
        text="çok gizli kullanıcı metni",
        catalog_id="cat-x",
        locale="tr-TR",
        embedding_profile_id="p1",
        catalog_revision=3,
    )
    key = build_cache_key(query, policy)
    assert "gizli" not in key
    assert "kullanıcı" not in key
    assert len(key) == 64


@pytest.mark.asyncio
async def test_cache_hit_returns_result_with_flag():
    service, catalog, embedding_repo, outbox, worker, matcher = await _build_stack()
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="mobile",
        semantic_description="Mobile devices",
    )
    await service.add_localization(
        category_id=category.id,
        locale=catalog.primary_locale,
        display_name="Mobil",
        synonyms=("telefon",),
    )
    await service.add_alias(
        category_id=category.id,
        locale=catalog.primary_locale,
        alias_text="telefon",
    )
    await service.publish_revision(catalog.id)
    await _publish_and_embed(service, catalog, outbox, worker)

    query = MatchQuery(
        text="telefon lazım",
        catalog_id=catalog.id,
        locale=catalog.primary_locale,
        embedding_profile_id="p1",
        catalog_revision=1,
    )
    first = await matcher.match(query)
    second = await matcher.match(query)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.selected_category_id == first.selected_category_id
