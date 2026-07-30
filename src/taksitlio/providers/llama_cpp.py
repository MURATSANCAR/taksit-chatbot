"""llama.cpp OpenAI-compatible provider (chat + embeddings).

Endpoint URL and model_reference always come from ai_model_profiles — never hardcoded.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from taksitlio.model_gateway.gateway import (
    CompletionRequest,
    CompletionResult,
    ModelGatewayError,
    ModelProfile,
)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def resolve_chat_url(profile: ModelProfile) -> str:
    """Use profile.endpoint_url as-is when it already points to chat completions."""
    url = profile.endpoint_url.rstrip("/")
    if url.endswith("/chat/completions") or url.endswith("/completions"):
        return profile.endpoint_url
    cfg = profile.configuration or {}
    path = str(cfg.get("chat_path") or "/v1/chat/completions")
    return urljoin(_origin(profile.endpoint_url) + "/", path.lstrip("/"))


def resolve_embedding_url(profile: ModelProfile) -> str:
    cfg = profile.configuration or {}
    explicit = cfg.get("embedding_endpoint_url")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    url = profile.endpoint_url.rstrip("/")
    if url.endswith("/embeddings"):
        return profile.endpoint_url
    path = str(cfg.get("embedding_path") or "/v1/embeddings")
    return urljoin(_origin(profile.endpoint_url) + "/", path.lstrip("/"))


def resolve_health_url(profile: ModelProfile) -> str:
    cfg = profile.configuration or {}
    explicit = cfg.get("health_endpoint_url")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return urljoin(_origin(profile.endpoint_url) + "/", "health")


class LlamaCppProvider:
    """Talks to a llama.cpp server using the OpenAI-compatible HTTP API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def health_check(self, profile: ModelProfile) -> Mapping[str, Any]:
        url = resolve_health_url(profile)
        timeout_s = max(profile.timeout_ms / 1000.0, 1.0)
        try:
            response = await self._client.get(url, timeout=timeout_s)
            response.raise_for_status()
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = {"status": "ok", "raw": response.text}
            return {"ok": True, "profile_code": profile.profile_code, "body": body}
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "profile_code": profile.profile_code,
                "error": str(exc),
            }

    async def chat_completion(
        self,
        profile: ModelProfile,
        request: CompletionRequest,
    ) -> CompletionResult:
        if profile.provider_type not in {"LLAMA_CPP", "OPENAI_COMPAT", "VLLM"}:
            raise ModelGatewayError(
                f"LlamaCppProvider cannot serve provider_type={profile.provider_type}"
            )

        payload = self._build_chat_payload(profile, request)
        timeout_s = (request.timeout_ms or profile.timeout_ms) / 1000.0
        url = resolve_chat_url(profile)
        started = time.perf_counter()

        try:
            response = await self._client.post(url, json=payload, timeout=timeout_s)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ModelGatewayError(
                f"llama.cpp timeout on '{profile.profile_code}' after {int(timeout_s * 1000)}ms"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelGatewayError(
                f"llama.cpp HTTP error on '{profile.profile_code}': {exc}"
            ) from exc

        content = self._extract_content(body)
        return CompletionResult(
            profile_code=profile.profile_code,
            content=content,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            raw=body if isinstance(body, dict) else {"body": body},
        )

    async def embed(
        self,
        profile: ModelProfile,
        texts: Sequence[str],
    ) -> tuple[list[list[float]], float]:
        if not texts:
            return [], 0.0
        if profile.task_type not in {"EMBEDDING", "OTHER"} and profile.provider_type not in {
            "LLAMA_CPP",
            "OPENAI_COMPAT",
            "EMBEDDING",
            "VLLM",
        }:
            raise ModelGatewayError(
                f"Profile '{profile.profile_code}' is not an embedding-capable profile"
            )

        payload: dict[str, Any] = {
            "model": profile.model_reference,
            "input": list(texts),
        }
        timeout_s = profile.timeout_ms / 1000.0
        url = resolve_embedding_url(profile)
        started = time.perf_counter()

        try:
            response = await self._client.post(url, json=payload, timeout=timeout_s)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ModelGatewayError(
                f"llama.cpp embedding timeout on '{profile.profile_code}'"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelGatewayError(
                f"llama.cpp embedding HTTP error on '{profile.profile_code}': {exc}"
            ) from exc

        vectors = self._extract_embeddings(body, expected=len(texts))
        latency_ms = (time.perf_counter() - started) * 1000.0
        return vectors, latency_ms

    @staticmethod
    def _build_chat_payload(
        profile: ModelProfile,
        request: CompletionRequest,
    ) -> dict[str, Any]:
        cfg = profile.configuration or {}
        payload: dict[str, Any] = {
            "model": profile.model_reference,
            "messages": request.messages,
            "temperature": (
                request.temperature
                if request.temperature is not None
                else float(profile.temperature)
            ),
            "max_tokens": request.max_tokens or profile.max_output_tokens,
            "stream": bool(cfg.get("streaming_enabled", False)),
        }
        # FAST path: thinking must stay off when configured
        if cfg.get("thinking_enabled") is False:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)
        elif cfg.get("json_schema_required"):
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _extract_content(body: Any) -> str:
        if not isinstance(body, dict):
            raise ModelGatewayError("llama.cpp response is not a JSON object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelGatewayError("llama.cpp response has no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ModelGatewayError("llama.cpp response missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("llama.cpp response content is empty")
        return content.strip()

    @staticmethod
    def _extract_embeddings(body: Any, *, expected: int) -> list[list[float]]:
        if not isinstance(body, dict):
            raise ModelGatewayError("llama.cpp embedding response is not a JSON object")
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise ModelGatewayError(
                f"llama.cpp embedding count mismatch: expected {expected}"
            )
        vectors: list[list[float]] = []
        for item in sorted(data, key=lambda row: row.get("index", 0) if isinstance(row, dict) else 0):
            if not isinstance(item, dict):
                raise ModelGatewayError("Invalid embedding row")
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise ModelGatewayError("Empty embedding vector")
            vectors.append([float(x) for x in embedding])
        return vectors
