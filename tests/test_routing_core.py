"""Comprehensive routing-core unit tests (production hardening)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from unittest.mock import AsyncMock

import pytest

from taksitlio.audit import AuditService, InMemoryAuditStore
from taksitlio.conversation.patch import ConversationPatchError, apply_conversation_patch
from taksitlio.model_gateway.gateway import ModelGateway
from taksitlio.model_gateway.repository import make_connection, make_deployment, make_profile
from taksitlio.model_gateway.types import (
    CompletionResult,
    JsonParseError,
    ModelDeployment,
    ProviderUnavailableError,
)
from taksitlio.model_router.confidence import SystemConfidenceEvaluator
from taksitlio.model_router.deadline import Deadline
from taksitlio.model_router.health import (
    CircuitState,
    HealthState,
    InMemoryRuntimeHealthRegistry,
)
from taksitlio.model_router.route_selector import (
    RouteContext,
    RouteVersion,
    select_route_version,
)
from taksitlio.model_router.router import ModelRouter
from taksitlio.model_router.router_types import (
    ConfidencePolicy,
    ReasonCode,
    RouteDecision,
    TimeoutPolicy,
    UnderstandingRequest,
)


def _valid_need(**overrides: Any) -> dict[str, Any]:
    base = {
        "intent": {"type": "PRODUCT_PURCHASE", "confidence": 0.97},
        "need_description": "kamera kalitesi iyi mobil cihaz",
        "budget": {
            "type": "APPROXIMATE",
            "value": 40000,
            "minimum": None,
            "maximum": None,
            "monthly_payment": None,
            "currency": "TRY",
        },
        "preferences": [{"concept": "camera_quality", "importance": 0.88}],
        "usage_context": [],
        "entities": [],
        "ambiguities": [],
        "clarification": {"required": False, "question_intent": None},
        "confidence": 0.95,
        "signals": {},
    }
    base.update(overrides)
    return base


def _deployments() -> tuple[ModelDeployment, ModelDeployment]:
    fast_p = make_profile(id=1, profile_code="FAST_UNDERSTANDING")
    deep_p = make_profile(id=2, profile_code="DEEP_UNDERSTANDING", timeout_ms=8000)
    # Opaque service DNS — no loopback IP literals in app/test fixtures for prod code paths.
    # Tests mock gateway HTTP; these URLs are never contacted.
    fast_c = make_connection(id=1, connection_code="C_FAST", base_url="http://test-fast")
    deep_c = make_connection(id=2, connection_code="C_DEEP", base_url="http://test-deep")
    primary = make_deployment(
        id=10, deployment_code="D_FAST", profile=fast_p, connection=fast_c
    )
    fallback = make_deployment(
        id=20, deployment_code="D_DEEP", profile=deep_p, connection=deep_c
    )
    return primary, fallback


@dataclass
class _Repo:
    routes: Sequence[RouteVersion]

    def list_active(self, task_code: str) -> Sequence[RouteVersion]:
        return [r for r in self.routes if r.task_code == task_code and r.is_active]


def _route(
    primary: ModelDeployment,
    fallback: ModelDeployment | None,
    *,
    version: int = 1,
    traffic_weight: float = 1.0,
    priority: int = 100,
    condition: dict[str, Any] | None = None,
    min_conf: float = 0.78,
) -> RouteVersion:
    return RouteVersion(
        id=version,
        task_code="NEED_UNDERSTANDING",
        route_version=version,
        display_name=f"route-{version}",
        primary=primary,
        fallback=fallback,
        confidence_policy=ConfidencePolicy(
            policy_code="DEFAULT",
            minimum_system_confidence=min_conf,
        ),
        timeout_policy=TimeoutPolicy(policy_code="DEFAULT"),
        condition_expression=condition or {"locale": "tr-TR", "client": "MOBILE"},
        traffic_weight=traffic_weight,
        priority=priority,
        is_active=True,
    )


def _mock_gateway(primary_payload: dict[str, Any] | Exception, fallback_payload: dict[str, Any] | Exception | None = None):
    gateway = AsyncMock(spec=ModelGateway)

    async def complete_json(deployment, request, *, deadline=None):
        if deployment.deployment_code == "D_FAST":
            if isinstance(primary_payload, Exception):
                raise primary_payload
            result = CompletionResult(
                deployment_code=deployment.deployment_code,
                profile_code=deployment.profile.profile_code,
                content="{}",
                latency_ms=100.0,
                correlation_id=request.correlation_id,
            )
            return primary_payload, result
        if isinstance(fallback_payload, Exception):
            raise fallback_payload
        assert fallback_payload is not None
        result = CompletionResult(
            deployment_code=deployment.deployment_code,
            profile_code=deployment.profile.profile_code,
            content="{}",
            latency_ms=200.0,
            correlation_id=request.correlation_id,
        )
        return fallback_payload, result

    gateway.complete_json = AsyncMock(side_effect=complete_json)
    return gateway


@pytest.mark.asyncio
async def test_fast_valid_high_system_confidence_continue():
    primary, fallback = _deployments()
    gateway = _mock_gateway(_valid_need())
    router = ModelRouter(gateway, _Repo([_route(primary, fallback)]))
    result = await router.understand(
        UnderstandingRequest(
            message="telefon 40 bin",
            session_id="s1",
            route_context={"locale": "tr-TR", "client": "MOBILE"},
        )
    )
    assert result.decision == RouteDecision.CONTINUE
    assert result.reason_code == ReasonCode.OK
    assert result.system_confidence is not None
    assert result.system_confidence >= 0.78
    assert result.fallback_used is False


@pytest.mark.asyncio
async def test_high_model_confidence_but_budget_inconsistent_does_not_continue():
    primary, fallback = _deployments()
    bad = _valid_need(
        confidence=0.99,
        budget={
            "type": "RANGE",
            "value": 50000,
            "minimum": 10000,
            "maximum": 20000,
            "monthly_payment": None,
            "currency": "TRY",
        },
    )
    # Fallback returns a repaired valid profile so we can observe non-CONTINUE on primary path
    good = _valid_need(confidence=0.9)
    gateway = _mock_gateway(bad, good)
    router = ModelRouter(gateway, _Repo([_route(primary, fallback)]))
    result = await router.understand(
        UnderstandingRequest(
            message="x",
            session_id="s1",
            route_context={"locale": "tr-TR", "client": "MOBILE"},
        )
    )
    assert result.decision != RouteDecision.CONTINUE or result.fallback_used
    # Primary must not be accepted as CONTINUE without fallback
    if not result.fallback_used:
        assert result.decision in {RouteDecision.FALLBACK, RouteDecision.CLARIFY, RouteDecision.SAFE_FAILURE}
    else:
        assert result.decision == RouteDecision.CONTINUE
        assert result.used_deployment_code == "D_DEEP"


@pytest.mark.asyncio
async def test_missing_user_information_clarifies_not_fallback():
    primary, fallback = _deployments()
    payload = _valid_need(
        need_description="okul için taşınabilir bir şey",
        clarification={"required": True, "question_intent": "device_type"},
        ambiguities=[{"code": "MISSING_PRODUCT_FORM", "description": "laptop/tablet"}],
        confidence=0.92,
    )
    gateway = _mock_gateway(payload)
    router = ModelRouter(gateway, _Repo([_route(primary, fallback)]))
    result = await router.understand(
        UnderstandingRequest(
            message="okul için taşınabilir bir şey arıyorum",
            session_id="s1",
            route_context={"locale": "tr-TR", "client": "MOBILE"},
        )
    )
    assert result.decision == RouteDecision.CLARIFY
    assert result.reason_code in {
        ReasonCode.MISSING_INFORMATION,
        ReasonCode.MISSING_PRODUCT_FORM,
    }
    assert gateway.complete_json.await_count == 1


@pytest.mark.asyncio
async def test_invalid_json_triggers_fallback():
    primary, fallback = _deployments()
    good = _valid_need()
    gateway = _mock_gateway(JsonParseError("bad json"), good)
    router = ModelRouter(gateway, _Repo([_route(primary, fallback)]))
    result = await router.understand(
        UnderstandingRequest(
            message="x",
            session_id="s1",
            route_context={"locale": "tr-TR", "client": "MOBILE"},
        )
    )
    assert result.fallback_used is True
    assert result.decision == RouteDecision.CONTINUE
    assert result.used_deployment_code == "D_DEEP"


@pytest.mark.asyncio
async def test_primary_runtime_unhealthy_goes_fallback():
    primary, fallback = _deployments()
    health = InMemoryRuntimeHealthRegistry()
    health.mark_unavailable(primary.id)
    good = _valid_need()
    gateway = _mock_gateway(ProviderUnavailableError("down"), good)
    # Router checks health before call
    router = ModelRouter(
        gateway,
        _Repo([_route(primary, fallback)]),
        health=health,
    )
    result = await router.understand(
        UnderstandingRequest(
            message="x",
            session_id="s1",
            route_context={"locale": "tr-TR", "client": "MOBILE"},
        )
    )
    assert result.reason_code == ReasonCode.PRIMARY_UNAVAILABLE or result.fallback_used
    assert result.used_deployment_code == "D_DEEP"


@pytest.mark.asyncio
async def test_deadline_exhausted_skips_fallback():
    primary, fallback = _deployments()
    calls = {"n": 0}

    async def complete_json(deployment, request):
        calls["n"] += 1
        if calls["n"] == 1:
            result = CompletionResult(
                deployment_code=deployment.deployment_code,
                profile_code=deployment.profile.profile_code,
                content="{}",
                latency_ms=5.0,
            )
            # Low system confidence → router wants FALLBACK, but budget already gone
            return _valid_need(confidence=0.1, signals={"indirect_or_complex": True}), result
        raise AssertionError("fallback must not be called")

    gateway = AsyncMock(spec=ModelGateway)
    gateway.complete_json = AsyncMock(side_effect=complete_json)

    route = RouteVersion(
        id=1,
        task_code="NEED_UNDERSTANDING",
        route_version=1,
        display_name="tight-deadline",
        primary=primary,
        fallback=fallback,
        confidence_policy=ConfidencePolicy(
            policy_code="DEFAULT", minimum_system_confidence=0.99
        ),
        timeout_policy=TimeoutPolicy(
            policy_code="DEFAULT",
            primary_timeout_ms=1,
            fallback_timeout_ms=8000,
            total_budget_ms=1,
            min_fallback_remaining_ms=500,
        ),
        condition_expression={"locale": "tr-TR", "client": "MOBILE"},
        traffic_weight=1.0,
        priority=100,
        is_active=True,
    )
    router = ModelRouter(gateway, _Repo([route]))
    result = await router.understand(
        UnderstandingRequest(
            message="x",
            session_id="s1",
            route_context={"locale": "tr-TR", "client": "MOBILE"},
        )
    )
    assert result.decision == RouteDecision.SAFE_FAILURE
    assert result.reason_code == ReasonCode.DEADLINE_EXHAUSTED
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_fallback_also_fails_safe_failure():
    primary, fallback = _deployments()
    gateway = _mock_gateway(JsonParseError("bad"), JsonParseError("also bad"))
    router = ModelRouter(gateway, _Repo([_route(primary, fallback)]))
    result = await router.understand(
        UnderstandingRequest(
            message="x",
            session_id="s1",
            route_context={"locale": "tr-TR", "client": "MOBILE"},
        )
    )
    assert result.decision == RouteDecision.SAFE_FAILURE
    assert result.reason_code == ReasonCode.FALLBACK_FAILED
    assert result.fallback_used is True


def test_route_condition_matching_and_weighted_challenger_deterministic():
    primary, fallback = _deployments()
    challenger_p = make_profile(id=3, profile_code="FAST_CHALLENGER")
    challenger_c = make_connection(id=3, connection_code="C_CH", base_url="http://test-ch")
    challenger = make_deployment(
        id=30,
        deployment_code="D_CHALLENGER",
        profile=challenger_p,
        connection=challenger_c,
    )
    control = _route(primary, fallback, version=1, traffic_weight=0.9, priority=100)
    challenge = _route(
        challenger, fallback, version=2, traffic_weight=0.1, priority=100
    )
    ctx = RouteContext(locale="tr-TR", client="MOBILE", session_id="sess-42")
    # Same seed → stable arm
    a = select_route_version([control, challenge], ctx, seed="sess-42")
    b = select_route_version([control, challenge], ctx, seed="sess-42")
    assert a.primary.deployment_code == b.primary.deployment_code

    # Non-matching condition excluded
    only_web = _route(
        primary,
        fallback,
        version=3,
        condition={"locale": "tr-TR", "client": "WEB"},
    )
    picked = select_route_version([only_web, control], ctx, seed="x")
    assert picked.route_version == 1


def test_conversation_patch_allowlist_and_ignores_old_value():
    current = {
        "need_description": "telefon",
        "budget": {"type": "APPROXIMATE", "value": 40000, "currency": "TRY"},
    }
    with pytest.raises(ConversationPatchError):
        apply_conversation_patch(
            current,
            {
                "operation": "SET",
                "path": "/budget/value",
                "value": 50000,
                "confidence": 0.9,
                "old_value": 40000,
            },
        )

    ok = apply_conversation_patch(
        current,
        {
            "operation": "SET",
            "path": "/budget/value",
            "value": 50000,
            "evidence_text": "bütçeyi 50'ye çıkarabiliriz",
            "confidence": 0.98,
        },
    )
    assert ok["budget"]["value"] == 50000
    assert ok["need_description"] == "telefon"

    with pytest.raises(ConversationPatchError):
        apply_conversation_patch(
            current,
            {
                "operation": "SET",
                "path": "/__proto__/x",
                "value": 1,
                "confidence": 1.0,
            },
        )


@pytest.mark.asyncio
async def test_audit_record_created():
    store = InMemoryAuditStore()
    audit = AuditService(store)
    record = await audit.model_activated(
        actor_id="admin-1",
        profile_code="FAST_UNDERSTANDING",
        before={"status": "CHALLENGER"},
        after={"status": "ACTIVE"},
        reason="promotion",
    )
    assert record.action == "ACTIVATE"
    listed = await store.list_for_entity("ai_model_profiles", "FAST_UNDERSTANDING")
    assert len(listed) == 1
    assert listed[0].before_value == {"status": "CHALLENGER"}


def test_system_confidence_not_equal_to_model_self_score():
    evaluator = SystemConfidenceEvaluator(model_weight=0.15)
    result = evaluator.evaluate(
        _valid_need(
            confidence=0.99,
            budget={
                "type": "RANGE",
                "value": 90000,
                "minimum": 10000,
                "maximum": 20000,
                "monthly_payment": None,
                "currency": "TRY",
            },
        ),
        schema_valid=True,
    )
    assert result.model_reported_confidence == 0.99
    assert result.system_confidence < 0.78
    assert result.signals.budget_consistent is False


def test_deadline_clamp():
    d = Deadline.from_budget_ms(1000)
    assert d.clamp_timeout_ms(5000) <= 1000
    # Exhaust
    object.__setattr__(d, "started_at", d.started_at - 2.0)
    assert d.is_exhausted()
    assert d.clamp_timeout_ms(3000) == 0


def test_no_localhost_or_vendor_model_names_in_routing_package():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "taksitlio"
    forbidden = re.compile(
        r"127\.0\.0\.1|localhost:\d+|Qwen3|0\.0\.0\.0:\d+",
        re.IGNORECASE,
    )
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if forbidden.search(text):
            offenders.append(str(path.relative_to(root.parent.parent)))
    assert offenders == []


def test_circuit_open_not_callable():
    health = InMemoryRuntimeHealthRegistry()
    health.mark_ready(1)
    health.set_circuit(1, CircuitState.OPEN)
    assert health.get(1).is_callable() is False
    health.set_circuit(1, CircuitState.CLOSED)
    health.get(1).health = HealthState.READY
    assert health.get(1).is_callable() is True
