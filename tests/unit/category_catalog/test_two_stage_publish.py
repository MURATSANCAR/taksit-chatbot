"""Two-stage publish invariants (ADR-004 + ADR-005).

Covers:

* embeddings not READY → cannot PUBLISH (raises CatalogEmbeddingsNotReady)
* old published revision remains readable while a new PREPARING revision is
  under embedding
* atomic switch: after publish, previous revision transitions to SUPERSEDED
* SemanticMatchPolicyMapper legacy → canonical mapping
"""

from __future__ import annotations

import pytest

from taksitlio.category_catalog import (
    CategoryCatalogService,
    InMemoryCategoryCatalogRepository,
    RepositoryEmbeddingReadinessChecker,
    RevisionStatus,
)
from taksitlio.category_catalog.errors import (
    CatalogEmbeddingsNotReady,
    CatalogRevisionNotReady,
)
from taksitlio.category_catalog.publish_pipeline import prepare_embed_and_publish
from taksitlio.category_embedding import (
    CategoryEmbeddingOutbox,
    CategoryEmbeddingWorker,
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.embeddings.client import LexicalEmbedder
from taksitlio.semantic_matching.policy import (
    SemanticMatchPolicy,
    SemanticMatchPolicyMapper,
)


class _EmbClient:
    async def embed(self, texts):
        return await LexicalEmbedder(dim=32).embed(list(texts))


class _NeverReadyEmbClient:
    async def embed(self, texts):
        raise RuntimeError("embedding server down")


async def _bootstrap_catalog():
    repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(repo)
    catalog = await service.create_catalog(catalog_code="TS", display_name="TS")
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="thing",
        semantic_description="something concrete for tests",
    )
    await service.add_localization(
        category_id=category.id,
        locale=catalog.primary_locale,
        display_name="Thing",
        synonyms=("alpha",),
    )
    await service.add_alias(
        category_id=category.id,
        locale=catalog.primary_locale,
        alias_text="alpha",
    )
    return service, repo, catalog, category


@pytest.mark.asyncio
async def test_publish_refused_until_embeddings_ready():
    service, repo, catalog, _ = await _bootstrap_catalog()
    embedding_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embedding_repo)
    # Never-ready embedding client: enqueues jobs, worker fails permanently
    class _AlwaysFail:
        async def embed(self, texts):
            raise RuntimeError("boom")

    worker = CategoryEmbeddingWorker(embedding_repo, _AlwaysFail())

    pending = await service.prepare_revision(catalog.id)
    snapshot = await service.get_revision_snapshot(catalog.id, pending)
    assert snapshot is not None
    await outbox.enqueue_for_snapshot(snapshot, embedding_profile_id="p1")
    # Exhaust retries so no READY record exists
    for _ in range(6):
        await worker.run_once()

    checker = RepositoryEmbeddingReadinessChecker(embedding_repo)
    with pytest.raises(CatalogEmbeddingsNotReady):
        await service.mark_ready_to_publish(
            catalog.id,
            pending,
            embedding_profile_id="p1",
            embedding_checker=checker,
        )
    with pytest.raises((CatalogEmbeddingsNotReady, CatalogRevisionNotReady)):
        await service.publish_revision(
            catalog.id,
            pending,
            embedding_profile_id="p1",
            embedding_checker=checker,
            require_embeddings=True,
        )
    catalog_after = await service.get_catalog(catalog.id)
    # No prior publish yet → still 0
    assert catalog_after.published_revision == 0


@pytest.mark.asyncio
async def test_previous_published_readable_while_new_revision_prepares():
    service, repo, catalog, category = await _bootstrap_catalog()
    embedding_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embedding_repo)
    worker = CategoryEmbeddingWorker(embedding_repo, _EmbClient())

    revision_1 = await prepare_embed_and_publish(
        service, catalog.id, outbox, worker, embedding_repo=embedding_repo
    )
    assert revision_1 == 1
    snapshot_1 = await service.get_published_snapshot(catalog.id)
    assert snapshot_1 is not None
    assert snapshot_1.revision == revision_1

    # Add another category and prepare revision 2 without publishing
    another = await service.add_category(
        catalog_id=catalog.id,
        slug="delta",
        semantic_description="delta description",
    )
    await service.add_localization(
        category_id=another.id,
        locale=catalog.primary_locale,
        display_name="Delta",
    )
    revision_2 = await service.prepare_revision(catalog.id)
    assert revision_2 == 2

    # Published pointer should still point at revision 1
    reread = await service.get_published_snapshot(catalog.id)
    assert reread is not None
    assert reread.revision == revision_1


@pytest.mark.asyncio
async def test_atomic_switch_supersedes_previous_published():
    service, repo, catalog, _ = await _bootstrap_catalog()
    embedding_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embedding_repo)
    worker = CategoryEmbeddingWorker(embedding_repo, _EmbClient())

    rev1 = await prepare_embed_and_publish(
        service, catalog.id, outbox, worker, embedding_repo=embedding_repo
    )
    # publish a second revision
    await service.add_category(
        catalog_id=catalog.id,
        slug="second",
        semantic_description="another",
    )
    later = await service._repo.list_categories(catalog.id)
    second = [c for c in later if c.slug == "second"][0]
    await service.add_localization(
        category_id=second.id,
        locale=catalog.primary_locale,
        display_name="Second",
    )
    rev2 = await prepare_embed_and_publish(
        service, catalog.id, outbox, worker, embedding_repo=embedding_repo
    )
    assert rev2 > rev1

    prev_record = await service._repo.get_revision(catalog.id, rev1)
    assert prev_record is not None
    assert prev_record.status == RevisionStatus.SUPERSEDED
    current = await service._repo.get_revision(catalog.id, rev2)
    assert current is not None
    assert current.status == RevisionStatus.PUBLISHED


def test_policy_mapper_bridges_legacy_columns():
    row = {
        "minimum_score": 0.55,
        "clarify_score_gap": 0.09,
        "minimum_auto_select_score": 0.7,
    }
    policy = SemanticMatchPolicyMapper.from_storage(row)
    assert policy.minimum_candidate_score == 0.55
    assert policy.minimum_auto_select_gap == 0.09
    storage = SemanticMatchPolicyMapper.to_storage(policy)
    assert "minimum_score" in storage
    assert "clarify_score_gap" in storage
