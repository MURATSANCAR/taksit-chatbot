"""Async LLM understanding worker + role-scoped circuit breaker (ADR-011 P1).

Does not invent product/finance facts. Patch provider is injectable.
Frontend sees only platform_role=UNDERSTANDING_SERVICE.

Remote provider prefers FAST_C (9B) / UNDERSTANDING_* when configured;
otherwise DeterministicFallbackProvider keeps local/demo paths green.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

import httpx

from taksitlio.llm_routing import (
    LlmJobStatus,
    PLATFORM_ROLE,
    validate_llm_patch,
)
from taksitlio.llm_routing.remote_provider import (
    EmptyResponse,
    UnderstandingDeploymentUnavailable,
    build_remote_understanding_from_env,
)
from taksitlio.runtime_verification.circuit import CircuitBreakerController, CircuitBreakerPolicy
from taksitlio.search_sessions.orchestrator import SearchOrchestrator

logger = logging.getLogger(__name__)


class UnderstandingPatchProvider(Protocol):
    async def understand(self, input_payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class DeterministicFallbackProvider:
    """No remote model — returns safe empty preferences so deterministic path continues."""

    provider_mode: str = "deterministic_fallback"

    async def understand(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        parse = ((input_payload.get("deterministic_parse") or {}).get("parse")) or {}
        return {
            "intent": parse.get("intent") or "PRODUCT_SEARCH",
            "confirmed_constraints": [],
            "inferred_preferences": [],
            "rejected_constraints": [],
            "unresolved_fields": list(parse.get("unresolved_spans") or []),
            "clarification": None,
            "safe_to_retrieve": True,
            "overall_confidence": float(parse.get("confidence") or 0.55),
        }


@dataclass
class UnderstandingCircuit:
    """Named-role circuit (deployment_id synthetic for UNDERSTANDING_SERVICE)."""

    controller: CircuitBreakerController
    deployment_id: int = 91011
    config_active: bool = True
    platform_role: str = PLATFORM_ROLE

    def is_open(self) -> bool:
        self.controller.maybe_transition(self.deployment_id)
        return not self.controller.is_routable(
            self.deployment_id, config_active=self.config_active
        )

    def success(self, latency_ms: float) -> None:
        self.controller.record_success(self.deployment_id, latency_ms=latency_ms)

    def failure(self, latency_ms: float = 0.0) -> None:
        self.controller.record_failure(self.deployment_id, latency_ms=latency_ms)


@dataclass
class LlmUnderstandingWorker:
    orchestrator: SearchOrchestrator
    provider: UnderstandingPatchProvider
    circuit: UnderstandingCircuit
    max_attempts: int = 1
    metrics: dict[str, list[float]] = field(default_factory=dict)
    provider_mode: str = "deterministic_fallback"

    def _record(self, name: str, value: float, *, session_id: Optional[str] = None) -> None:
        self.metrics.setdefault(name, []).append(value)
        from taksitlio.search_sessions.metrics import GLOBAL_SEARCH_METRICS

        if name.endswith("_ms"):
            GLOBAL_SEARCH_METRICS.observe(name, value)
        else:
            GLOBAL_SEARCH_METRICS.incr(name, int(value) if value == int(value) else 1)
        if session_id and name.endswith("_ms"):
            self.orchestrator.repo.record_metric(session_id, name, value)

    async def process_job(self, job_id: str) -> dict[str, Any]:
        job = self.orchestrator.llm_jobs.get(job_id)
        if job is None:
            raise KeyError("job_not_found")
        if job.status not in {LlmJobStatus.QUEUED, LlmJobStatus.RUNNING}:
            return {"status": job.status.value, "applied": False}

        if self.circuit.is_open():
            self.orchestrator.circuit_open = True
            # Deterministic fallback without pretending LLM succeeded
            session = self.orchestrator.repo.get(job.search_session_id)
            if session is None:
                return {"status": "FAILED", "applied": False, "reason": "circuit_open"}
            parse = self.orchestrator.parses[job.search_session_id]
            result = self.orchestrator._fast_retrieve(session, parse, degraded=True)
            self._record("circuit_open_fallback", 1.0)
            return {"status": "CIRCUIT_OPEN", "applied": True, "result": result}

        job.status = LlmJobStatus.RUNNING
        started = time.perf_counter()
        attempt = 0
        last_error: Optional[str] = None
        while attempt <= self.max_attempts:
            attempt += 1
            try:
                patch = await self.provider.understand(job.input_payload)
                validate_llm_patch(patch)
                latency = (time.perf_counter() - started) * 1000.0
                self.circuit.success(latency)
                self._record("llm_inference_ms", latency, session_id=job.search_session_id)
                session = self.orchestrator.repo.get(job.search_session_id)
                if session is None:
                    return {"status": "FAILED", "applied": False}
                if job.query_version != session.active_query_version:
                    job.status = LlmJobStatus.STALE_RESULT
                    self._record("stale_llm_result", 1.0)
                    return {"status": "STALE_RESULT", "applied": False}
                out = self.orchestrator.complete_llm_job(job_id, patch)
                out["provider_mode"] = self.provider_mode
                return out
            except Exception as exc:  # noqa: BLE001
                last_error = type(exc).__name__
                if attempt > self.max_attempts:
                    break
                # one retry only for transport-like failures
                if last_error not in {
                    "TimeoutError",
                    "ConnectionError",
                    "OSError",
                    "EmptyResponse",
                }:
                    break

        latency = (time.perf_counter() - started) * 1000.0
        self.circuit.failure(latency)
        job.status = LlmJobStatus.FAILED
        job.error_code = last_error or "FAILED"
        job.completed_at = datetime.now(timezone.utc)
        session = self.orchestrator.repo.get(job.search_session_id)
        parse = self.orchestrator.parses[job.search_session_id]
        result = self.orchestrator._fast_retrieve(session, parse, degraded=True)  # type: ignore[arg-type]
        self._record("llm_failed_fallback", 1.0)
        return {
            "status": "FAILED",
            "applied": True,
            "result": result,
            "error": last_error,
            "provider_mode": self.provider_mode,
        }

    async def drain_once(self) -> list[dict[str, Any]]:
        results = []
        for job_id, job in list(self.orchestrator.llm_jobs.items()):
            if job.status in {LlmJobStatus.QUEUED, LlmJobStatus.RUNNING}:
                # Only process jobs still matching active version
                session = self.orchestrator.repo.get(job.search_session_id)
                if session and job.query_version != session.active_query_version:
                    job.status = LlmJobStatus.STALE_RESULT
                    self._record("stale_llm_result", 1.0)
                    continue
                results.append(await self.process_job(job_id))
        return results


def build_understanding_provider(
    *,
    http_client: Optional[httpx.AsyncClient] = None,
    prefer_remote: bool = True,
) -> tuple[UnderstandingPatchProvider, str]:
    """Build provider; remote when env configured, else deterministic fallback."""

    if prefer_remote:
        try:
            remote = build_remote_understanding_from_env(client=http_client)
            return remote, remote.provider_mode
        except UnderstandingDeploymentUnavailable:
            pass
    fallback = DeterministicFallbackProvider()
    return fallback, fallback.provider_mode


def build_default_worker(
    orchestrator: SearchOrchestrator,
    *,
    health_registry: Any,
    http_client: Optional[httpx.AsyncClient] = None,
    prefer_remote: bool = True,
) -> LlmUnderstandingWorker:
    circuit = UnderstandingCircuit(
        controller=CircuitBreakerController(
            health_registry, policy=CircuitBreakerPolicy(failure_threshold=3)
        )
    )
    provider, mode = build_understanding_provider(
        http_client=http_client, prefer_remote=prefer_remote
    )
    return LlmUnderstandingWorker(
        orchestrator=orchestrator,
        provider=provider,
        circuit=circuit,
        provider_mode=mode,
    )


def schedule_llm_job(worker: Optional[LlmUnderstandingWorker], job_id: Optional[str]) -> None:
    """Fire-and-forget process for a queued understanding job (chat / search API)."""

    if worker is None or not job_id:
        return

    async def _run() -> None:
        try:
            await worker.process_job(job_id)
        except Exception:  # noqa: BLE001
            logger.exception("understanding job failed job_id=%s", job_id)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run())


# Re-export for callers / tests
__all__ = [
    "DeterministicFallbackProvider",
    "EmptyResponse",
    "LlmUnderstandingWorker",
    "UnderstandingCircuit",
    "UnderstandingPatchProvider",
    "build_default_worker",
    "build_understanding_provider",
    "schedule_llm_job",
]
