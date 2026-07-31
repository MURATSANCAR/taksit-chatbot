"""Async LLM understanding worker + role-scoped circuit breaker (ADR-011 P1).

Does not invent product/finance facts. Patch provider is injectable.
Frontend sees only platform_role=UNDERSTANDING_SERVICE.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from taksitlio.llm_routing import (
    LlmJobStatus,
    PLATFORM_ROLE,
    validate_llm_patch,
)
from taksitlio.runtime_verification.circuit import CircuitBreakerController, CircuitBreakerPolicy
from taksitlio.search_sessions.orchestrator import SearchOrchestrator


class UnderstandingPatchProvider(Protocol):
    async def understand(self, input_payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class DeterministicFallbackProvider:
    """No remote model — returns safe empty preferences so deterministic path continues."""

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

    def _record(self, name: str, value: float) -> None:
        self.metrics.setdefault(name, []).append(value)

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
                self._record("llm_inference_ms", latency)
                session = self.orchestrator.repo.get(job.search_session_id)
                if session is None:
                    return {"status": "FAILED", "applied": False}
                if job.query_version != session.active_query_version:
                    job.status = LlmJobStatus.STALE_RESULT
                    self._record("stale_llm_result", 1.0)
                    return {"status": "STALE_RESULT", "applied": False}
                return self.orchestrator.complete_llm_job(job_id, patch)
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
        return {"status": "FAILED", "applied": True, "result": result, "error": last_error}

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


def build_default_worker(
    orchestrator: SearchOrchestrator,
    *,
    health_registry: Any,
) -> LlmUnderstandingWorker:
    circuit = UnderstandingCircuit(
        controller=CircuitBreakerController(
            health_registry, policy=CircuitBreakerPolicy(failure_threshold=3)
        )
    )
    return LlmUnderstandingWorker(
        orchestrator=orchestrator,
        provider=DeterministicFallbackProvider(),
        circuit=circuit,
    )
