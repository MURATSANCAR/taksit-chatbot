"""Circuit breaker helpers over RuntimeHealthRegistry (ADR-009 §12)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from taksitlio.model_gateway.health import (
    CircuitState,
    HealthState,
    RuntimeHealthRegistry,
    RuntimeSnapshot,
)


@dataclass
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    success_threshold: int = 1
    open_cooldown_seconds: float = 30.0
    half_open_max_probes: int = 1


class CircuitBreakerController:
    """Timeout / error driven OPEN → cooldown HALF_OPEN → success CLOSED.

    Config ACTIVE alone never routes traffic when runtime is UNAVAILABLE or
    circuit is OPEN.
    """

    def __init__(
        self,
        registry: RuntimeHealthRegistry,
        *,
        policy: CircuitBreakerPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy or CircuitBreakerPolicy()
        self._consecutive_failures: dict[int, int] = {}
        self._consecutive_successes: dict[int, int] = {}
        self._opened_at: dict[int, float] = {}
        self._half_open_probes: dict[int, int] = {}

    def record_success(self, deployment_id: int, *, latency_ms: float) -> None:
        self._registry.end_request(deployment_id, success=True, latency_ms=latency_ms)
        self._consecutive_failures[deployment_id] = 0
        succ = self._consecutive_successes.get(deployment_id, 0) + 1
        self._consecutive_successes[deployment_id] = succ
        snap = self._registry.get(deployment_id)
        if snap.circuit_state == CircuitState.HALF_OPEN:
            if succ >= self._policy.success_threshold:
                self._registry.set_circuit(deployment_id, CircuitState.CLOSED)
                self._half_open_probes[deployment_id] = 0
                self._registry.mark_ready(deployment_id)

    def record_failure(self, deployment_id: int, *, latency_ms: float = 0.0) -> None:
        self._registry.end_request(deployment_id, success=False, latency_ms=latency_ms)
        self._consecutive_successes[deployment_id] = 0
        fail = self._consecutive_failures.get(deployment_id, 0) + 1
        self._consecutive_failures[deployment_id] = fail
        if fail >= self._policy.failure_threshold:
            self._registry.set_circuit(deployment_id, CircuitState.OPEN)
            self._registry.mark_unavailable(deployment_id)
            self._opened_at[deployment_id] = time.time()
            self._half_open_probes[deployment_id] = 0

    def maybe_transition(self, deployment_id: int) -> RuntimeSnapshot:
        snap = self._registry.get(deployment_id)
        if snap.circuit_state != CircuitState.OPEN:
            return snap
        opened = self._opened_at.get(deployment_id, 0.0)
        if time.time() - opened >= self._policy.open_cooldown_seconds:
            self._registry.set_circuit(deployment_id, CircuitState.HALF_OPEN)
            snap = self._registry.get(deployment_id)
            snap.health = HealthState.WARMING_UP
        return snap

    def is_routable(self, deployment_id: int, *, config_active: bool = True) -> bool:
        if not config_active:
            return False
        snap = self.maybe_transition(deployment_id)
        if snap.health == HealthState.UNAVAILABLE and snap.circuit_state == CircuitState.OPEN:
            return False
        if snap.circuit_state == CircuitState.OPEN:
            return False
        if snap.circuit_state == CircuitState.HALF_OPEN:
            used = self._half_open_probes.get(deployment_id, 0)
            if used >= self._policy.half_open_max_probes:
                return False
            self._half_open_probes[deployment_id] = used + 1
            return True
        return snap.is_callable()
