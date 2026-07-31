"""Unit + acceptance coverage for ADR-009 provisional / runtime gates."""

from __future__ import annotations

import pytest

from taksitlio.model_gateway.health import (
    CircuitState,
    HealthState,
    InMemoryRuntimeHealthRegistry,
)
from taksitlio.runtime_verification.circuit import CircuitBreakerController
from taksitlio.runtime_verification.dependencies import (
    DependencyCode,
    DependencyProbeResult,
    RuntimeDependencyReport,
)
from taksitlio.runtime_verification.evidence import RuntimeEvidence
from taksitlio.runtime_verification.gate import (
    CampaignGateStatus,
    RuntimeGateStatus,
    evaluate_campaign_gate,
    evaluate_provisional_gate,
    evaluate_runtime_gate,
)
from taksitlio.evaluation.evaluator import _resolve_final_status
from taksitlio.evaluation.domain import QualityGateStatus


def _ok_probe() -> DependencyProbeResult:
    return DependencyProbeResult(code=None, available=True, measured=True, detail="ok")


def _bad(code: DependencyCode) -> DependencyProbeResult:
    return DependencyProbeResult(code=code, available=False, measured=True, detail="down")


def _full_evidence(**overrides) -> RuntimeEvidence:
    base = dict(
        real_redis_measured=True,
        real_pgvector_measured=True,
        real_fast_measured=True,
        real_embedding_measured=True,
        redis_integration_skipped=0,
        pgvector_integration_skipped=0,
        human_reviewed_count=120,
        oracle_top_1=0.91,
        oracle_top_2=1.0,
        oracle_required=1.0,
        oracle_forbidden=0,
        oracle_unsafe=0,
        e2e_status=0.84,
        e2e_top_1=0.68,
        e2e_top_2=0.90,
        e2e_required=0.88,
        e2e_forbidden=0,
        e2e_unsafe=0,
        fast_invalid_schema_count=0,
        fast_forbidden_identifier_count=0,
        fast_negative_constraint_recall=0.96,
        fast_correction_recall=0.92,
    )
    base.update(overrides)
    return RuntimeEvidence(**base)


def test_missing_dependency_is_blocked() -> None:
    deps = RuntimeDependencyReport(
        redis=_ok_probe(),
        postgres=_ok_probe(),
        pgvector=_ok_probe(),
        fast=_bad(DependencyCode.FAST_DEPLOYMENT_UNAVAILABLE),
        embedding=_ok_probe(),
    )
    assert evaluate_runtime_gate(deps) == RuntimeGateStatus.BLOCKED_DEPENDENCY


def test_quality_drop_is_runtime_quality_reject() -> None:
    deps = RuntimeDependencyReport(
        redis=_ok_probe(),
        postgres=_ok_probe(),
        pgvector=_ok_probe(),
        fast=_ok_probe(),
        embedding=_ok_probe(),
    )
    assert (
        evaluate_runtime_gate(deps, quality_ok=False)
        == RuntimeGateStatus.RUNTIME_QUALITY_REJECT
    )


def test_provisional_accept_when_all_conditions_met() -> None:
    result = evaluate_provisional_gate(_full_evidence())
    assert result.status == "PROVISIONAL_ACCEPT"
    assert result.runtime_gate == RuntimeGateStatus.RUNTIME_READY
    assert result.campaign_gate == CampaignGateStatus.READY_TO_OPEN


def test_campaign_closed_without_provisional_accept() -> None:
    result = evaluate_provisional_gate(_full_evidence(real_fast_measured=False))
    assert result.status == "BLOCKED_DEPENDENCY"
    assert evaluate_campaign_gate(result) == CampaignGateStatus.CLOSED


def test_evaluator_provisional_accept_with_runtime_evidence() -> None:
    status, notes = _resolve_final_status(
        True,
        {"HUMAN_REVIEWED": 200, "DRAFT": 0, "synthetic": 0, "total": 200},
        forbidden_count=0,
        unsafe_count=0,
        gate_profile="provisional",
        runtime_evidence=_full_evidence(),
    )
    assert status == QualityGateStatus.PROVISIONAL_ACCEPT
    assert any("ADR-009" in n for n in notes)


def test_evaluator_stays_blocked_without_runtime_evidence() -> None:
    status, _notes = _resolve_final_status(
        True,
        {"HUMAN_REVIEWED": 200, "DRAFT": 0, "synthetic": 0, "total": 200},
        forbidden_count=0,
        unsafe_count=0,
        gate_profile="provisional",
        runtime_evidence=None,
    )
    assert status == QualityGateStatus.QUALITY_READY_RUNTIME_BLOCKED


def test_circuit_breaker_open_half_open_closed() -> None:
    reg = InMemoryRuntimeHealthRegistry()
    ctl = CircuitBreakerController(reg)
    dep = 7
    reg.mark_ready(dep)
    for _ in range(3):
        reg.begin_request(dep)
        ctl.record_failure(dep)
    snap = reg.get(dep)
    assert snap.circuit_state == CircuitState.OPEN
    assert ctl.is_routable(dep, config_active=True) is False
    # Force cooldown elapsed
    ctl._opened_at[dep] = 0.0  # noqa: SLF001
    assert ctl.is_routable(dep, config_active=True) is True  # HALF_OPEN probe
    reg.begin_request(dep)
    ctl.record_success(dep, latency_ms=10.0)
    assert reg.get(dep).circuit_state == CircuitState.CLOSED
    # Config ACTIVE but UNAVAILABLE+OPEN still blocked (fresh open, no cooldown)
    import time as _time

    reg.mark_unavailable(dep)
    reg.set_circuit(dep, CircuitState.OPEN)
    ctl._opened_at[dep] = _time.time()  # noqa: SLF001
    ctl._half_open_probes[dep] = 0  # noqa: SLF001
    assert ctl.is_routable(dep, config_active=True) is False


def test_remote_fast_unavailable_from_empty_env(monkeypatch) -> None:
    from taksitlio.understanding.fast.errors import FastDeploymentUnavailable
    from taksitlio.understanding.fast.remote import build_remote_fast_from_env

    monkeypatch.delenv("FAST_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("POC_FAST_BASE_URL", raising=False)
    monkeypatch.delenv("FAST_MODEL_REFERENCE", raising=False)
    monkeypatch.delenv("POC_FAST_MODEL_REFERENCE", raising=False)
    with pytest.raises(FastDeploymentUnavailable):
        build_remote_fast_from_env()


def test_strict_embedder_rejects_empty_and_has_no_lexical_fallback(monkeypatch) -> None:
    import asyncio

    from taksitlio.embeddings.strict_client import (
        EmbeddingDeploymentUnavailable,
        EmbeddingInputError,
        StrictOpenAICompatibleEmbedder,
        build_strict_embedder_from_env,
    )

    monkeypatch.delenv("EMBEDDING_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("POC_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL_REFERENCE", raising=False)
    monkeypatch.delenv("POC_EMBEDDING_MODEL_REFERENCE", raising=False)
    monkeypatch.delenv("EMBEDDING_DIM", raising=False)
    monkeypatch.delenv("POC_EMBEDDING_DIM", raising=False)
    with pytest.raises(EmbeddingDeploymentUnavailable):
        build_strict_embedder_from_env()

    emb = StrictOpenAICompatibleEmbedder(
        base_url="http://embedder.invalid",
        model_reference="alias",
        expected_dimension=4,
    )

    async def _empty():
        with pytest.raises(EmbeddingInputError):
            await emb.embed([])

    asyncio.run(_empty())


def test_fast_quality_scoring_and_comparison() -> None:
    from taksitlio.evaluation.runtime import compare_to_baseline, score_fast_extraction

    metrics = score_fast_extraction(
        [
            {
                "expected_need_profile": {
                    "intent": {"type": "PRODUCT_PURCHASE"},
                    "budget": {"type": "UNKNOWN"},
                    "clarification": {"required": False},
                },
                "predicted_need_profile": {
                    "intent": {"type": "PRODUCT_PURCHASE"},
                    "budget": {"type": "UNKNOWN"},
                    "clarification": {"required": False},
                },
                "expected_constraints": {
                    "positive": [{"surface_form": "kalem"}],
                    "negative": [{"surface_form": "saat"}],
                    "corrections": [],
                },
                "predicted_constraints": {
                    "positive": [{"surface_form": "kalem"}],
                    "negative": [{"surface_form": "saat"}],
                    "corrections": [],
                },
            }
        ]
    )
    assert metrics.invalid_schema_count == 0
    assert metrics.negative_constraint_recall == 1.0

    cmp = compare_to_baseline(
        {"top_1": 0.9, "forbidden": 0, "unsafe": 0},
        {"top_1": 0.88, "forbidden": 0, "unsafe": 0},
    )
    assert cmp.safety_regression is False
    assert cmp.deltas["top_1"] == pytest.approx(-0.02)
