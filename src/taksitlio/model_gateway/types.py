"""Typed domain models for model profiles, connections, and deployments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelProfile:
    """Inference behaviour settings — never a live network endpoint."""

    id: int
    profile_code: str
    display_name: str
    provider_type: str
    model_reference: str
    task_type: str
    context_limit: int
    max_output_tokens: int
    temperature: float
    timeout_ms: int
    parallel_slots: int
    status: str
    configuration: Mapping[str, Any] = field(default_factory=dict)
    # DEPRECATED — present only for legacy DB rows; gateway must ignore.
    endpoint_url: str | None = None


@dataclass(frozen=True)
class ProviderConnection:
    id: int
    connection_code: str
    provider_type: str
    base_url: str
    credential_ref: str | None = None
    configuration: Mapping[str, Any] = field(default_factory=dict)
    status: str = "ACTIVE"


@dataclass(frozen=True)
class ModelDeployment:
    id: int
    deployment_code: str
    profile: ModelProfile
    connection: ProviderConnection
    runtime_alias: str
    priority: int = 100
    traffic_weight: float = 1.0
    max_parallel_requests: int | None = None
    status: str = "ACTIVE"
    configuration: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompletionRequest:
    messages: list[dict[str, str]]
    response_format: Mapping[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_ms: int | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class CompletionResult:
    deployment_code: str
    profile_code: str
    content: str
    latency_ms: float
    correlation_id: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class ModelGatewayError(Exception):
    """Base provider failure."""

    error_class: str = "GATEWAY_ERROR"

    def __init__(self, message: str, *, error_class: str | None = None) -> None:
        super().__init__(message)
        if error_class:
            self.error_class = error_class


class ProviderTimeoutError(ModelGatewayError):
    error_class = "PROVIDER_TIMEOUT"


class ProviderUnavailableError(ModelGatewayError):
    error_class = "PROVIDER_UNAVAILABLE"


class ProviderHttpError(ModelGatewayError):
    error_class = "PROVIDER_HTTP"


class JsonParseError(ModelGatewayError):
    error_class = "JSON_PARSE"


class ResponseTooLargeError(ModelGatewayError):
    error_class = "RESPONSE_TOO_LARGE"


class DeadlineExhaustedError(ModelGatewayError):
    error_class = "DEADLINE_EXHAUSTED"


class DeploymentNotCallableError(ModelGatewayError):
    error_class = "DEPLOYMENT_NOT_CALLABLE"
