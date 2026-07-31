from taksitlio.embeddings.client import Embedder, LexicalEmbedder, ProfileEmbedder
from taksitlio.embeddings.strict_client import (
    EmbeddingDeploymentUnavailable,
    EmbeddingDimensionError,
    EmbeddingInputError,
    StrictOpenAICompatibleEmbedder,
    build_strict_embedder_from_env,
)
from taksitlio.embeddings.vectors import cosine_similarity, l2_normalize

__all__ = [
    "Embedder",
    "EmbeddingDeploymentUnavailable",
    "EmbeddingDimensionError",
    "EmbeddingInputError",
    "LexicalEmbedder",
    "ProfileEmbedder",
    "StrictOpenAICompatibleEmbedder",
    "build_strict_embedder_from_env",
    "cosine_similarity",
    "l2_normalize",
]
