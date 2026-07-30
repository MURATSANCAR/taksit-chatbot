"""Category embedding package."""

from taksitlio.category_embedding.domain import (
    CATEGORY_PROJECTION_KIND,
    CategoryEmbeddingJob,
    CategoryEmbeddingRecord,
    EmbeddingJobStatus,
    SemanticDocument,
)
from taksitlio.category_embedding.in_memory_repository import (
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.category_embedding.outbox import CategoryEmbeddingOutbox
from taksitlio.category_embedding.projector import CategorySemanticProjector
from taksitlio.category_embedding.worker import (
    CategoryEmbeddingWorker,
    EmbeddingClient,
    WorkerRunSummary,
)

__all__ = [
    "CATEGORY_PROJECTION_KIND",
    "CategoryEmbeddingJob",
    "CategoryEmbeddingOutbox",
    "CategoryEmbeddingRecord",
    "CategoryEmbeddingWorker",
    "CategorySemanticProjector",
    "EmbeddingClient",
    "EmbeddingJobStatus",
    "InMemoryCategoryEmbeddingRepository",
    "SemanticDocument",
    "WorkerRunSummary",
]
