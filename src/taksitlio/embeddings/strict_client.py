"""Strict OpenAI-compatible embedding client — no lexical fallback (ADR-009)."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Optional, Sequence

import httpx

from taksitlio.embeddings.vectors import l2_normalize


class EmbeddingDeploymentUnavailable(Exception):
    """Real embedding deployment missing — never substitute LexicalEmbedder."""

    reason_code = "EMBEDDING_DEPLOYMENT_UNAVAILABLE"

    def __init__(self, message: str = "embedding deployment unavailable") -> None:
        super().__init__(message)


class EmbeddingDimensionError(Exception):
    reason_code = "EMBEDDING_DIMENSION_MISMATCH"


class EmbeddingInputError(Exception):
    reason_code = "EMBEDDING_INPUT_INVALID"


class StrictOpenAICompatibleEmbedder:
    """Batch embedder with dimension checks and content-hash idempotency helpers.

    ``fallback_lexical`` is intentionally absent — failures raise typed errors.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_reference: str,
        expected_dimension: int,
        timeout_ms: int = 5000,
        max_batch_size: int = 64,
        embedding_path: str = "/v1/embeddings",
        api_key: Optional[str] = None,
        normalize: bool = True,
        profile_code: str = "CATEGORY_EMBEDDING",
        deployment_code: str = "runtime-embedding",
        space_id: str = "default",
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise EmbeddingDeploymentUnavailable("embedding base_url empty")
        if not model_reference or not model_reference.strip():
            raise EmbeddingDeploymentUnavailable("embedding model_reference empty")
        if expected_dimension <= 0:
            raise EmbeddingInputError("expected_dimension must be > 0")
        self._base_url = base_url.rstrip("/")
        self._model_reference = model_reference
        self._dim = expected_dimension
        self._timeout_ms = timeout_ms
        self._max_batch = max_batch_size
        self._path = embedding_path if embedding_path.startswith("/") else f"/{embedding_path}"
        self._api_key = api_key
        self._normalize = normalize
        self.profile_code = profile_code
        self.deployment_code = deployment_code
        self.space_id = space_id
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self.last_latency_ms: float = 0.0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            raise EmbeddingInputError("empty embedding input rejected")
        if len(texts) > self._max_batch:
            raise EmbeddingInputError(
                f"batch size {len(texts)} exceeds max_batch_size={self._max_batch}"
            )
        for t in texts:
            if not isinstance(t, str) or not t.strip():
                raise EmbeddingInputError("blank embedding input rejected")

        url = f"{self._base_url}{self._path}"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"model": self._model_reference, "input": list(texts)}
        started = time.perf_counter()
        try:
            response = await self._client.post(
                url,
                json=payload,
                headers=headers,
                timeout=max(self._timeout_ms / 1000.0, 0.5),
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise EmbeddingDeploymentUnavailable(
                f"embedding timeout after {self._timeout_ms}ms"
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingDeploymentUnavailable(f"embedding HTTP error: {exc}") from exc

        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        vectors = self._extract(body, expected=len(texts))
        for vec in vectors:
            if len(vec) != self._dim:
                raise EmbeddingDimensionError(
                    f"dimension {len(vec)} != expected {self._dim}"
                )
        if self._normalize:
            return [l2_normalize(v) for v in vectors]
        return vectors

    @staticmethod
    def _extract(body: object, *, expected: int) -> list[list[float]]:
        if not isinstance(body, dict):
            raise EmbeddingDeploymentUnavailable("embedding response not JSON object")
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise EmbeddingDeploymentUnavailable(
                f"embedding count mismatch: expected {expected}"
            )
        out: list[list[float]] = []
        for item in sorted(
            data, key=lambda row: row.get("index", 0) if isinstance(row, dict) else 0
        ):
            if not isinstance(item, dict):
                raise EmbeddingDeploymentUnavailable("invalid embedding row")
            emb = item.get("embedding")
            if not isinstance(emb, list) or not emb:
                raise EmbeddingDeploymentUnavailable("empty embedding vector")
            out.append([float(x) for x in emb])
        return out


def build_strict_embedder_from_env() -> StrictOpenAICompatibleEmbedder:
    base = (
        os.environ.get("EMBEDDING_PROVIDER_BASE_URL")
        or os.environ.get("POC_EMBEDDING_BASE_URL")
        or ""
    ).strip()
    model = (
        os.environ.get("EMBEDDING_MODEL_REFERENCE")
        or os.environ.get("POC_EMBEDDING_MODEL_REFERENCE")
        or ""
    ).strip()
    dim_raw = os.environ.get("EMBEDDING_DIM") or os.environ.get("POC_EMBEDDING_DIM") or ""
    if not base or not model or not dim_raw:
        raise EmbeddingDeploymentUnavailable(
            "EMBEDDING_PROVIDER_BASE_URL / EMBEDDING_MODEL_REFERENCE / EMBEDDING_DIM required"
        )
    return StrictOpenAICompatibleEmbedder(
        base_url=base,
        model_reference=model,
        expected_dimension=int(dim_raw),
        timeout_ms=int(os.environ.get("EMBEDDING_TIMEOUT_MS") or "5000"),
        max_batch_size=int(os.environ.get("EMBEDDING_MAX_BATCH") or "64"),
        api_key=os.environ.get("EMBEDDING_API_KEY") or None,
        profile_code=os.environ.get("EMBEDDING_PROFILE_CODE") or "CATEGORY_EMBEDDING",
        deployment_code=os.environ.get("EMBEDDING_DEPLOYMENT_CODE") or "runtime-embedding",
        space_id=os.environ.get("EMBEDDING_SPACE_ID") or "default",
    )


__all__ = [
    "EmbeddingDeploymentUnavailable",
    "EmbeddingDimensionError",
    "EmbeddingInputError",
    "StrictOpenAICompatibleEmbedder",
    "build_strict_embedder_from_env",
]
