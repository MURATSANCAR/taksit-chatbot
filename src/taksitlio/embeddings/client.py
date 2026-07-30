"""Embedding client — resolves profile from DB, never hardcodes model names."""

from __future__ import annotations

from typing import Protocol, Sequence

import httpx

from taksitlio.embeddings.vectors import bag_of_chars_embedding, l2_normalize
from taksitlio.model_gateway.gateway import ModelProfile
from taksitlio.providers.llama_cpp import LlamaCppProvider


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class ProfileEmbedder:
    def __init__(
        self,
        profile: ModelProfile,
        client: httpx.AsyncClient,
        *,
        fallback_lexical: bool = True,
    ) -> None:
        self._profile = profile
        self._provider = LlamaCppProvider(client)
        self._fallback_lexical = fallback_lexical
        cfg = profile.configuration or {}
        self._dim = int(cfg.get("embedding_dim") or 768)
        self._normalize = bool(cfg.get("normalize", True))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors, _ = await self._provider.embed(self._profile, texts)
            if self._normalize:
                return [l2_normalize(v) for v in vectors]
            return vectors
        except Exception:
            if not self._fallback_lexical:
                raise
            return [
                bag_of_chars_embedding(t, dim=self._dim) for t in texts
            ]


class LexicalEmbedder:
    """Offline embedder used in tests and when embedding server is unavailable."""

    def __init__(self, *, dim: int = 256) -> None:
        self._dim = dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [bag_of_chars_embedding(t, dim=self._dim) for t in texts]
