"""Canonical two-stage publish helper: prepare → embed → READY → PUBLISHED."""

from __future__ import annotations

from typing import Any, Optional

from taksitlio.category_catalog.embedding_gate import (
    AlwaysReadyEmbeddingChecker,
    EmbeddingReadinessChecker,
    RepositoryEmbeddingReadinessChecker,
)
from taksitlio.category_catalog.service import CategoryCatalogService
from taksitlio.category_embedding.outbox import CategoryEmbeddingOutbox
from taksitlio.category_embedding.worker import CategoryEmbeddingWorker


async def prepare_embed_and_publish(
    service: CategoryCatalogService,
    catalog_id: str,
    outbox: CategoryEmbeddingOutbox,
    worker: CategoryEmbeddingWorker,
    *,
    embedding_profile_id: str = "p1",
    embedding_repo: Any = None,
    embedding_checker: Optional[EmbeddingReadinessChecker] = None,
) -> int:
    """
    DRAFT → PREPARING (snapshot) → embedding jobs → READY_TO_PUBLISH → PUBLISHED.

    Does not mutate the previous published revision until the final atomic switch.
    """
    pending = await service.prepare_revision(catalog_id)
    snapshot = await service.get_revision_snapshot(catalog_id, pending)
    if snapshot is None:
        raise RuntimeError(f"missing snapshot for pending revision {pending}")
    await outbox.enqueue_for_snapshot(
        snapshot, embedding_profile_id=embedding_profile_id
    )
    await worker.run_once()
    checker: EmbeddingReadinessChecker
    if embedding_checker is not None:
        checker = embedding_checker
    elif embedding_repo is not None:
        checker = RepositoryEmbeddingReadinessChecker(embedding_repo)
    else:
        checker = AlwaysReadyEmbeddingChecker()
    await service.mark_ready_to_publish(
        catalog_id,
        pending,
        embedding_profile_id=embedding_profile_id,
        embedding_checker=checker,
    )
    await service.publish_revision(
        catalog_id,
        pending,
        embedding_profile_id=embedding_profile_id,
        embedding_checker=checker,
    )
    return pending


__all__ = ["prepare_embed_and_publish"]
