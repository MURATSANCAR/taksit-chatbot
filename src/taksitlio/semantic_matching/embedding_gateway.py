"""Embedding gateway used by the matcher for the query vector.

The gateway is intentionally decoupled from the category embedding worker.
The worker embeds catalog projections; the gateway embeds live user queries.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from taksitlio.embeddings.client import Embedder
from taksitlio.embeddings.vectors import bag_of_chars_embedding
from taksitlio.semantic_matching.errors import EmbeddingGatewayUnavailable


class QueryEmbeddingGateway(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...


class EmbedderQueryGateway:
    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    async def embed_query(self, text: str) -> list[float]:
        try:
            vectors = await self._embedder.embed([text])
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingGatewayUnavailable(str(exc)) from exc
        if not vectors:
            raise EmbeddingGatewayUnavailable("empty embedding response")
        return list(vectors[0])


class LexicalFallbackGateway:
    """Deterministic gateway used in tests / degraded flows."""

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    async def embed_query(self, text: str) -> list[float]:
        return bag_of_chars_embedding(text or "", dim=self._dim)


class AlwaysFailingGateway:
    """Test helper that simulates an unavailable embedding server."""

    async def embed_query(self, text: str) -> list[float]:
        raise EmbeddingGatewayUnavailable("embedding server unavailable")


__all__ = [
    "AlwaysFailingGateway",
    "EmbedderQueryGateway",
    "LexicalFallbackGateway",
    "QueryEmbeddingGateway",
]
