"""Unit tests for category embedding projector, outbox, and worker."""

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
    CategorySemanticProjector,
    EmbeddingJobStatus,
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.embeddings.client import LexicalEmbedder


class _StubEmbedder:
    def __init__(self, dim: int = 32) -> None:
        self._inner = LexicalEmbedder(dim=dim)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._inner.embed(list(texts))


class _FailingEmbedder:
    def __init__(self, fail_times: int) -> None:
        self._remaining = fail_times

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("embed server offline")
        return await LexicalEmbedder(dim=32).embed(list(texts))


async def _publish_simple_snapshot():
    repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(repo)
    catalog = await service.create_catalog(catalog_code="EMB", display_name="EMB")
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="node",
        semantic_description="A node used by the projector test",
    )
    await service.add_localization(
        category_id=category.id,
        locale=catalog.primary_locale,
        display_name="Node",
        synonyms=("alpha",),
    )
    await service.add_alias(
        category_id=category.id,
        locale=catalog.primary_locale,
        alias_text="alias-embed",
        alias_type=MatchMode.EXACT,
    )
    await service.add_use_case(
        category_id=category.id,
        locale=catalog.primary_locale,
        use_case_text="use case for embedding",
    )
    await service.publish_revision(catalog.id)
    snapshot = await service.get_published_snapshot(catalog.id)
    assert snapshot is not None
    return snapshot


def test_projection_text_is_deterministic():
    projector = CategorySemanticProjector()

    class _Node:
        id = "n1"
        catalog_id = "c1"
        slug = "n1"
        parent_id = None
        depth = 0
        display_name = "Dummy"
        description = "desc"
        semantic_description = "summary"
        synonyms = ("b", "a")
        aliases = ()
        use_cases = ()
        locale = "tr-TR"
        ancestor_ids = ()

    doc = projector.project(_Node(), catalog_revision=1)
    assert doc.projection_text
    assert doc.content_hash
    assert len(doc.content_hash) == 64
    # Sorted synonyms so hash is order-independent
    other = projector.project(_Node(), catalog_revision=1)
    assert doc.content_hash == other.content_hash


@pytest.mark.asyncio
async def test_outbox_dedupes_jobs_by_content_hash():
    snapshot = await _publish_simple_snapshot()
    repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(repo)
    first = await outbox.enqueue_for_snapshot(
        snapshot, embedding_profile_id="profile-1"
    )
    second = await outbox.enqueue_for_snapshot(
        snapshot, embedding_profile_id="profile-1"
    )
    assert len(first) == len(snapshot.nodes) == 1
    assert first[0].id == second[0].id  # dedupe returns the same job
    pending = await repo.list_pending_jobs()
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_worker_marks_previous_stale_on_new_content_hash():
    snapshot = await _publish_simple_snapshot()
    repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(repo)

    await outbox.enqueue_for_snapshot(snapshot, embedding_profile_id="profile-1")
    worker = CategoryEmbeddingWorker(repo, _StubEmbedder())
    summary = await worker.run_once()
    assert summary.ready == 1

    node = snapshot.nodes[0]
    record = await repo.get_embedding(
        category_id=node.id,
        catalog_revision=snapshot.revision,
        locale=snapshot.locale,
        embedding_profile_id="profile-1",
    )
    assert record is not None

    # Simulate a new projection with different content_hash by forging a job.
    from taksitlio.category_embedding.domain import CategoryEmbeddingJob, new_id

    forged = CategoryEmbeddingJob(
        id=new_id(),
        category_id=node.id,
        catalog_revision=snapshot.revision,
        locale=snapshot.locale,
        embedding_profile_id="profile-1",
        content_hash="different-hash",
        projection_text="different projection text",
    )
    await repo.enqueue_job(forged)
    await CategoryEmbeddingWorker(repo, _StubEmbedder()).run_once()

    ready = await repo.list_ready(
        catalog_revision=snapshot.revision,
        locale=snapshot.locale,
        embedding_profile_id="profile-1",
    )
    assert len(list(ready)) == 1
    latest = await repo.get_embedding(
        category_id=node.id,
        catalog_revision=snapshot.revision,
        locale=snapshot.locale,
        embedding_profile_id="profile-1",
    )
    assert latest is not None
    assert latest.content_hash == "different-hash"


@pytest.mark.asyncio
async def test_worker_bounded_retry():
    snapshot = await _publish_simple_snapshot()
    repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(repo, projector=CategorySemanticProjector())
    await outbox.enqueue_for_snapshot(
        snapshot, embedding_profile_id="profile-1", max_attempts=2
    )
    worker = CategoryEmbeddingWorker(repo, _FailingEmbedder(fail_times=5))
    for _ in range(3):
        await worker.run_once()
    pending = await repo.list_pending_jobs()
    all_states = [
        (await repo.get_job(j.id)).status for j in pending or []
    ]
    # After exceeding max_attempts, the job must have moved to FAILED.
    jobs = (await repo.dump_state())["jobs"]
    assert any(j.status == EmbeddingJobStatus.FAILED for j in jobs.values())
