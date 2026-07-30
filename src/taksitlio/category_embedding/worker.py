"""Embedding worker: consumes pending jobs, stores embedding records.

The worker uses an injectable `EmbeddingClient` protocol so tests can plug
in the deterministic lexical fallback without touching HTTP or model
inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from taksitlio.category_embedding.domain import (
    CategoryEmbeddingJob,
    CategoryEmbeddingRecord,
    EmbeddingJobStatus,
    new_id,
)
from taksitlio.category_embedding.in_memory_repository import (
    InMemoryCategoryEmbeddingRepository,
)


class EmbeddingClient(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class WorkerRunSummary:
    processed: int
    ready: int
    failed: int
    retried: int


class CategoryEmbeddingWorker:
    def __init__(
        self,
        repository: InMemoryCategoryEmbeddingRepository,
        client: EmbeddingClient,
    ) -> None:
        self._repo = repository
        self._client = client

    async def run_once(self, *, limit: int = 32) -> WorkerRunSummary:
        pending: list[CategoryEmbeddingJob] = await self._repo.list_pending_jobs(
            limit=limit
        )
        processed = 0
        ready = 0
        failed = 0
        retried = 0
        for job in pending:
            processed += 1
            attempts = job.attempts + 1
            try:
                vectors = await self._client.embed([job.projection_text])
            except Exception as exc:  # noqa: BLE001 — bounded retry policy
                if attempts >= job.max_attempts:
                    await self._repo.update_job_status(
                        job.id,
                        status=EmbeddingJobStatus.FAILED,
                        last_error=str(exc),
                        attempts=attempts,
                    )
                    failed += 1
                else:
                    await self._repo.update_job_status(
                        job.id,
                        status=EmbeddingJobStatus.PENDING,
                        last_error=str(exc),
                        attempts=attempts,
                    )
                    retried += 1
                continue

            if not vectors:
                await self._repo.update_job_status(
                    job.id,
                    status=EmbeddingJobStatus.FAILED,
                    last_error="empty embedding response",
                    attempts=attempts,
                )
                failed += 1
                continue

            vector = tuple(float(x) for x in vectors[0])
            record = CategoryEmbeddingRecord(
                id=new_id(),
                category_id=job.category_id,
                catalog_revision=job.catalog_revision,
                locale=job.locale,
                embedding_profile_id=job.embedding_profile_id,
                content_hash=job.content_hash,
                embedding=vector,
                embedding_dimension=len(vector),
                projection_text=job.projection_text,
                status=EmbeddingJobStatus.READY,
            )
            await self._repo.mark_previous_stale(
                category_id=job.category_id,
                catalog_revision=job.catalog_revision,
                locale=job.locale,
                embedding_profile_id=job.embedding_profile_id,
                exclude_content_hash=job.content_hash,
            )
            await self._repo.store_embedding(record)
            await self._repo.update_job_status(
                job.id,
                status=EmbeddingJobStatus.READY,
                attempts=attempts,
            )
            ready += 1
        return WorkerRunSummary(
            processed=processed, ready=ready, failed=failed, retried=retried
        )


__all__ = ["CategoryEmbeddingWorker", "EmbeddingClient", "WorkerRunSummary"]
