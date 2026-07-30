"""Dynamic ModelGateway — provider-agnostic inference against DB-backed profiles."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import httpx


@dataclass(frozen=True)
class ModelProfile:
    id: int
    profile_code: str
    display_name: str
    provider_type: str
    endpoint_url: str
    model_reference: str
    task_type: str
    context_limit: int
    max_output_tokens: int
    temperature: float
    timeout_ms: int
    parallel_slots: int
    status: str
    configuration: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompletionRequest:
    messages: list[dict[str, str]]
    response_format: Mapping[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_ms: int | None = None


@dataclass(frozen=True)
class CompletionResult:
    profile_code: str
    content: str
    latency_ms: float
    raw: Mapping[str, Any] = field(default_factory=dict)


class ProfileRepository(Protocol):
    def get_by_code(self, profile_code: str) -> ModelProfile: ...

    def get_by_id(self, profile_id: int) -> ModelProfile: ...


class ModelGatewayError(Exception):
    """Raised when a provider call fails or returns an unusable payload."""


class ModelGateway:
    """
    Executes inference using a ModelProfile loaded from ai_model_profiles.

    Application code must never hardcode model names; always resolve via profile_code
    or profile id from ai_task_routes.
    """

    def __init__(
        self,
        profiles: ProfileRepository,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._profiles = profiles
        self._client = client or httpx.AsyncClient()
        self._llama = None

    def _llama_provider(self):
        if self._llama is None:
            from taksitlio.providers.llama_cpp import LlamaCppProvider

            self._llama = LlamaCppProvider(self._client)
        return self._llama

    async def complete(
        self,
        profile: ModelProfile | str | int,
        request: CompletionRequest,
    ) -> CompletionResult:
        resolved = self._resolve(profile)
        if resolved.status not in {"ACTIVE", "CHALLENGER"}:
            raise ModelGatewayError(
                f"Profile '{resolved.profile_code}' is not callable (status={resolved.status})"
            )

        thinking = bool(resolved.configuration.get("thinking_enabled", False))
        if thinking:
            raise ModelGatewayError(
                f"Profile '{resolved.profile_code}' has thinking enabled; FAST path requires thinking off"
            )

        if resolved.provider_type in {"LLAMA_CPP", "OPENAI_COMPAT", "VLLM"}:
            return await self._llama_provider().chat_completion(resolved, request)

        payload = self._build_openai_compat_payload(resolved, request)
        timeout_s = (request.timeout_ms or resolved.timeout_ms) / 1000.0
        started = time.perf_counter()

        try:
            response = await self._client.post(
                resolved.endpoint_url,
                json=payload,
                timeout=timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ModelGatewayError(
                f"Timeout calling '{resolved.profile_code}' after {resolved.timeout_ms}ms"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelGatewayError(
                f"HTTP error calling '{resolved.profile_code}': {exc}"
            ) from exc

        content = self._extract_content(body)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return CompletionResult(
            profile_code=resolved.profile_code,
            content=content,
            latency_ms=latency_ms,
            raw=body if isinstance(body, dict) else {"body": body},
        )

    async def complete_json(
        self,
        profile: ModelProfile | str | int,
        request: CompletionRequest,
    ) -> tuple[dict[str, Any], CompletionResult]:
        result = await self.complete(profile, request)
        try:
            parsed = json.loads(result.content)
        except json.JSONDecodeError as exc:
            raise ModelGatewayError(
                f"Profile '{result.profile_code}' returned non-JSON content"
            ) from exc
        if not isinstance(parsed, dict):
            raise ModelGatewayError(
                f"Profile '{result.profile_code}' returned JSON that is not an object"
            )
        return parsed, result

    def _resolve(self, profile: ModelProfile | str | int) -> ModelProfile:
        if isinstance(profile, ModelProfile):
            return profile
        if isinstance(profile, str):
            return self._profiles.get_by_code(profile)
        return self._profiles.get_by_id(profile)

    @staticmethod
    def _build_openai_compat_payload(
        profile: ModelProfile,
        request: CompletionRequest,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": profile.model_reference,
            "messages": request.messages,
            "temperature": (
                request.temperature
                if request.temperature is not None
                else float(profile.temperature)
            ),
            "max_tokens": request.max_tokens or profile.max_output_tokens,
            "stream": False,
        }
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)
        elif profile.configuration.get("json_schema_required"):
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _extract_content(body: Any) -> str:
        if not isinstance(body, dict):
            raise ModelGatewayError("Provider response is not a JSON object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelGatewayError("Provider response has no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ModelGatewayError("Provider response missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("Provider response content is empty")
        return content.strip()
