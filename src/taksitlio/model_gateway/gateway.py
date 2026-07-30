"""Deployment-resolved ModelGateway (OpenAI-compatible)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Mapping
from urllib.parse import urljoin

import httpx

from taksitlio.model_gateway.types import (
    CompletionRequest,
    CompletionResult,
    DeadlineExhaustedError,
    DeploymentNotCallableError,
    JsonParseError,
    ModelDeployment,
    ModelGatewayError,
    ProviderHttpError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ResponseTooLargeError,
)
from taksitlio.model_gateway.health import RuntimeHealthRegistry


DEFAULT_MAX_RESPONSE_BYTES = 256_000


class ModelGateway:
    """
    Calls a concrete ModelDeployment via its ProviderConnection.

    Never reads deprecated ModelProfile.endpoint_url.
    Never logs raw user message content.
    Caller (ModelRouter) must clamp timeout_ms against the absolute deadline.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        health: RuntimeHealthRegistry | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._client = client or httpx.AsyncClient()
        self._health = health
        self._max_response_bytes = max_response_bytes

    async def complete(
        self,
        deployment: ModelDeployment,
        request: CompletionRequest,
    ) -> CompletionResult:
        if deployment.status not in {"ACTIVE", "DRAINING"}:
            raise DeploymentNotCallableError(
                f"Deployment '{deployment.deployment_code}' status={deployment.status}"
            )
        if deployment.connection.status != "ACTIVE":
            raise ProviderUnavailableError(
                f"Connection '{deployment.connection.connection_code}' is not ACTIVE"
            )
        if deployment.profile.configuration.get("thinking_enabled") is True:
            raise ModelGatewayError(
                f"Deployment '{deployment.deployment_code}' has thinking enabled",
                error_class="INVALID_CONFIGURATION",
            )

        correlation_id = request.correlation_id or str(uuid.uuid4())
        timeout_ms = int(request.timeout_ms or deployment.profile.timeout_ms)
        if timeout_ms <= 0:
            raise DeadlineExhaustedError("Request deadline exhausted before provider call")

        if self._health is not None:
            snap = self._health.get(deployment.id)
            if not snap.is_callable():
                raise ProviderUnavailableError(
                    f"Deployment '{deployment.deployment_code}' runtime not callable"
                )
            self._health.begin_request(deployment.id)

        url = resolve_chat_url(deployment)
        payload = build_openai_compat_payload(deployment, request)
        started = time.perf_counter()
        try:
            response = await self._client.post(
                url,
                json=payload,
                timeout=timeout_ms / 1000.0,
                headers={"X-Correlation-ID": correlation_id},
            )
            raw_bytes = response.content
            if len(raw_bytes) > self._max_response_bytes:
                raise ResponseTooLargeError(
                    f"Provider response exceeds {self._max_response_bytes} bytes"
                )
            response.raise_for_status()
            body = response.json()
            content = extract_content(body)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if self._health is not None:
                self._health.end_request(deployment.id, success=True, latency_ms=latency_ms)
            return CompletionResult(
                deployment_code=deployment.deployment_code,
                profile_code=deployment.profile.profile_code,
                content=content,
                latency_ms=latency_ms,
                correlation_id=correlation_id,
                raw=body if isinstance(body, dict) else {"body": body},
            )
        except DeadlineExhaustedError:
            raise
        except ResponseTooLargeError:
            if self._health is not None:
                self._health.end_request(deployment.id, success=False, latency_ms=0.0)
            raise
        except httpx.TimeoutException as exc:
            if self._health is not None:
                self._health.end_request(deployment.id, success=False, latency_ms=0.0)
            raise ProviderTimeoutError(
                f"Timeout calling deployment '{deployment.deployment_code}'"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if self._health is not None:
                self._health.end_request(deployment.id, success=False, latency_ms=0.0)
            raise ProviderHttpError(
                f"HTTP {exc.response.status_code} from '{deployment.deployment_code}'"
            ) from exc
        except httpx.HTTPError as exc:
            if self._health is not None:
                self._health.end_request(deployment.id, success=False, latency_ms=0.0)
            raise ProviderUnavailableError(
                f"Provider error on '{deployment.deployment_code}'"
            ) from exc
        except ModelGatewayError:
            if self._health is not None:
                self._health.end_request(deployment.id, success=False, latency_ms=0.0)
            raise

    async def complete_json(
        self,
        deployment: ModelDeployment,
        request: CompletionRequest,
    ) -> tuple[dict[str, Any], CompletionResult]:
        result = await self.complete(deployment, request)
        try:
            parsed = json.loads(result.content)
        except json.JSONDecodeError as exc:
            raise JsonParseError(
                f"Deployment '{result.deployment_code}' returned non-JSON content"
            ) from exc
        if not isinstance(parsed, dict):
            raise JsonParseError(
                f"Deployment '{result.deployment_code}' returned JSON that is not an object"
            )
        return parsed, result


def resolve_chat_url(deployment: ModelDeployment) -> str:
    cfg = deployment.connection.configuration or {}
    path = str(cfg.get("chat_path") or "/v1/chat/completions")
    base = deployment.connection.base_url.rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def build_openai_compat_payload(
    deployment: ModelDeployment,
    request: CompletionRequest,
) -> dict[str, Any]:
    profile = deployment.profile
    cfg = profile.configuration or {}
    payload: dict[str, Any] = {
        "model": deployment.runtime_alias or profile.model_reference,
        "messages": request.messages,
        "temperature": (
            request.temperature
            if request.temperature is not None
            else float(profile.temperature)
        ),
        "max_tokens": request.max_tokens or profile.max_output_tokens,
        "stream": False,
    }
    if cfg.get("thinking_enabled") is False:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if request.response_format is not None:
        payload["response_format"] = dict(request.response_format)
    elif cfg.get("json_schema_required"):
        payload["response_format"] = {"type": "json_object"}
    return payload


def extract_content(body: Any) -> str:
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
