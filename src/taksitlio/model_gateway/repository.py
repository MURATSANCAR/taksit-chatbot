"""In-memory deployment catalog for tests — no real hosts or vendor model names."""

from __future__ import annotations

from typing import Iterable

from taksitlio.model_gateway.types import ModelDeployment, ModelProfile, ProviderConnection


def make_profile(
    *,
    id: int,
    profile_code: str,
    task_type: str = "UNDERSTANDING",
    status: str = "ACTIVE",
    timeout_ms: int = 3000,
    max_output_tokens: int = 128,
    temperature: float = 0.0,
) -> ModelProfile:
    return ModelProfile(
        id=id,
        profile_code=profile_code,
        display_name=profile_code,
        provider_type="OPENAI_COMPAT",
        model_reference=f"runtime:{profile_code}",
        task_type=task_type,
        context_limit=4096,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        timeout_ms=timeout_ms,
        parallel_slots=1,
        status=status,
        configuration={
            "thinking_enabled": False,
            "streaming_enabled": False,
            "json_schema_required": True,
        },
        endpoint_url=None,
    )


def make_connection(
    *,
    id: int,
    connection_code: str,
    base_url: str,
) -> ProviderConnection:
    return ProviderConnection(
        id=id,
        connection_code=connection_code,
        provider_type="OPENAI_COMPAT",
        base_url=base_url,
        configuration={"chat_path": "/v1/chat/completions"},
        status="ACTIVE",
    )


def make_deployment(
    *,
    id: int,
    deployment_code: str,
    profile: ModelProfile,
    connection: ProviderConnection,
    runtime_alias: str | None = None,
    priority: int = 100,
    traffic_weight: float = 1.0,
) -> ModelDeployment:
    return ModelDeployment(
        id=id,
        deployment_code=deployment_code,
        profile=profile,
        connection=connection,
        runtime_alias=runtime_alias or profile.profile_code,
        priority=priority,
        traffic_weight=traffic_weight,
        max_parallel_requests=4,
        status="ACTIVE",
    )


class InMemoryDeploymentCatalog:
    def __init__(self, deployments: Iterable[ModelDeployment]) -> None:
        self._by_code = {d.deployment_code: d for d in deployments}

    def get(self, deployment_code: str) -> ModelDeployment:
        try:
            return self._by_code[deployment_code]
        except KeyError as exc:
            raise KeyError(f"Unknown deployment_code: {deployment_code}") from exc
