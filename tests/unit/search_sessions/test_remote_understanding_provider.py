"""ADR-011 remote understanding provider (FAST_C / 9B) wiring tests."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from taksitlio.llm_routing.remote_provider import (
    EmptyResponse,
    RemoteUnderstandingProvider,
    UnderstandingDeploymentUnavailable,
    build_remote_understanding_from_env,
    resolve_understanding_endpoint_from_env,
)
from taksitlio.llm_routing.worker import (
    build_default_worker,
    build_understanding_provider,
)
from taksitlio.model_gateway.health import InMemoryRuntimeHealthRegistry
from taksitlio.search_sessions import build_demo_orchestrator


def _openai_json_response(patch: dict[str, Any], *, model: str = "poc-fast-nine-b") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(patch, ensure_ascii=False),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )


@pytest.mark.asyncio
async def test_remote_provider_returns_validated_patch() -> None:
    patch = {
        "intent": "PRODUCT_SEARCH",
        "overall_confidence": 0.81,
        "safe_to_retrieve": True,
        "confirmed_constraints": [],
        "inferred_preferences": [{"concept": "kompakt", "confidence": 0.7}],
        "rejected_constraints": [],
        "unresolved_fields": [],
        "clarification": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "poc-fast-nine-b"
        assert body["messages"][0]["role"] == "system"
        user = json.loads(body["messages"][1]["content"])
        assert user["user_message"] == "kompakt bir cihaz istiyorum"
        assert "untrusted_content_notice" in user
        return _openai_json_response(patch)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = RemoteUnderstandingProvider(
            base_url="http://fast-c.test",
            model_reference="poc-fast-nine-b",
            source_label="FAST_C",
            client=client,
        )
        out = await provider.understand(
            {
                "task": "UNDERSTAND_PRODUCT_NEED",
                "user_message": "kompakt bir cihaz istiyorum",
                "deterministic_parse": {},
            }
        )
    assert out["overall_confidence"] == 0.81
    assert out["inferred_preferences"][0]["concept"] == "kompakt"
    assert provider.provider_mode == "remote_nine_b"


@pytest.mark.asyncio
async def test_remote_provider_rejects_invented_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _openai_json_response(
            {
                "intent": "PRODUCT_SEARCH",
                "overall_confidence": 0.9,
                "safe_to_retrieve": True,
                "price": 12345,
            }
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = RemoteUnderstandingProvider(
            base_url="http://fast-c.test",
            model_reference="poc-fast-nine-b",
            client=client,
        )
        with pytest.raises(ValueError):
            await provider.understand({"user_message": "x"})


@pytest.mark.asyncio
async def test_remote_provider_empty_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = RemoteUnderstandingProvider(
            base_url="http://fast-c.test",
            model_reference="poc-fast-nine-b",
            client=client,
        )
        with pytest.raises(EmptyResponse):
            await provider.understand({"user_message": "x"})


def test_resolve_prefers_fast_c(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNDERSTANDING_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("UNDERSTANDING_MODEL_REFERENCE", raising=False)
    monkeypatch.setenv("FAST_C_BASE_URL", "http://nine-b.local")
    monkeypatch.setenv("FAST_C_MODEL_REFERENCE", "poc-fast-nine-b")
    monkeypatch.setenv("FAST_PROVIDER_BASE_URL", "http://primary.local")
    monkeypatch.setenv("FAST_MODEL_REFERENCE", "poc-fast-understanding")
    base, model, source = resolve_understanding_endpoint_from_env()
    assert source == "FAST_C"
    assert base == "http://nine-b.local"
    assert model == "poc-fast-nine-b"


def test_resolve_understanding_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNDERSTANDING_PROVIDER_BASE_URL", "http://override.local")
    monkeypatch.setenv("UNDERSTANDING_MODEL_REFERENCE", "custom-alias")
    monkeypatch.setenv("FAST_C_BASE_URL", "http://nine-b.local")
    monkeypatch.setenv("FAST_C_MODEL_REFERENCE", "poc-fast-nine-b")
    base, model, source = resolve_understanding_endpoint_from_env()
    assert source == "UNDERSTANDING"
    assert base == "http://override.local"
    assert model == "custom-alias"


def test_build_from_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "UNDERSTANDING_PROVIDER_BASE_URL",
        "UNDERSTANDING_MODEL_REFERENCE",
        "FAST_C_BASE_URL",
        "FAST_C_MODEL_REFERENCE",
        "FAST_C_RUNTIME_ALIAS",
        "FAST_PROVIDER_BASE_URL",
        "FAST_MODEL_REFERENCE",
        "POC_FAST_BASE_URL",
        "POC_FAST_MODEL_REFERENCE",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(UnderstandingDeploymentUnavailable):
        build_remote_understanding_from_env()


def test_build_provider_falls_back_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "UNDERSTANDING_PROVIDER_BASE_URL",
        "UNDERSTANDING_MODEL_REFERENCE",
        "FAST_C_BASE_URL",
        "FAST_C_MODEL_REFERENCE",
        "FAST_PROVIDER_BASE_URL",
        "FAST_MODEL_REFERENCE",
        "POC_FAST_BASE_URL",
        "POC_FAST_MODEL_REFERENCE",
    ):
        monkeypatch.delenv(key, raising=False)
    provider, mode = build_understanding_provider(prefer_remote=True)
    assert mode == "deterministic_fallback"
    assert getattr(provider, "provider_mode") == "deterministic_fallback"


@pytest.mark.asyncio
async def test_worker_uses_remote_nine_b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAST_C_BASE_URL", "http://nine-b.local")
    monkeypatch.setenv("FAST_C_MODEL_REFERENCE", "poc-fast-nine-b")

    patch = {
        "intent": "PRODUCT_SEARCH",
        "overall_confidence": 0.77,
        "safe_to_retrieve": True,
        "confirmed_constraints": [],
        "inferred_preferences": [{"concept": "hafif"}],
        "rejected_constraints": [],
        "unresolved_fields": [],
        "clarification": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return _openai_json_response(patch)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        orch = build_demo_orchestrator()
        out = orch.start(
            conversation_id="00000000-0000-0000-0000-000000000099",
            message=(
                "Evde herkes kullanacak, bazen film izlenecek, bazen çocuklar "
                "ödev yapacak ama cihazın odada çok yer kaplamasını istemiyorum. "
                "Aylık ödeme de zorlamasın, uzun vadede mantıklı bir şey olsun."
            ),
        )
        if out["route"] != "LLM":
            pytest.skip("did not route to LLM")
        worker = build_default_worker(
            orch,
            health_registry=InMemoryRuntimeHealthRegistry(),
            http_client=client,
            prefer_remote=True,
        )
        assert worker.provider_mode == "remote_nine_b"
        results = await worker.drain_once()
        assert results
        assert results[0].get("provider_mode") == "remote_nine_b"
        assert results[0]["status"] in {
            "COMPLETED",
            "STALE_RESULT",
            "CANCELLED",
            "FAILED",
            "CIRCUIT_OPEN",
        } or results[0].get("applied")
