"""Runtime health registry — config ACTIVE ≠ runtime READY."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class HealthState(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    WARMING_UP = "WARMING_UP"
    UNKNOWN = "UNKNOWN"


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class RuntimeSnapshot:
    deployment_id: int
    health: HealthState = HealthState.UNKNOWN
    active_requests: int = 0
    queue_depth: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    circuit_state: CircuitState = CircuitState.CLOSED
    last_seen_at: float = field(default_factory=time.time)
    last_error_at: float | None = None
    last_success_at: float | None = None

    def is_callable(self) -> bool:
        if self.circuit_state == CircuitState.OPEN:
            return False
        return self.health in {HealthState.READY, HealthState.DEGRADED, HealthState.WARMING_UP}


class RuntimeHealthRegistry(Protocol):
    def get(self, deployment_id: int) -> RuntimeSnapshot: ...

    def mark_ready(self, deployment_id: int) -> None: ...

    def mark_unavailable(self, deployment_id: int) -> None: ...

    def begin_request(self, deployment_id: int) -> None: ...

    def end_request(
        self,
        deployment_id: int,
        *,
        success: bool,
        latency_ms: float,
    ) -> None: ...

    def set_circuit(self, deployment_id: int, state: CircuitState) -> None: ...


class InMemoryRuntimeHealthRegistry:
    def __init__(self) -> None:
        self._data: dict[int, RuntimeSnapshot] = {}

    def get(self, deployment_id: int) -> RuntimeSnapshot:
        snap = self._data.get(deployment_id)
        if snap is None:
            snap = RuntimeSnapshot(deployment_id=deployment_id, health=HealthState.READY)
            self._data[deployment_id] = snap
        return snap

    def mark_ready(self, deployment_id: int) -> None:
        snap = self.get(deployment_id)
        snap.health = HealthState.READY
        snap.circuit_state = CircuitState.CLOSED
        snap.last_seen_at = time.time()

    def mark_unavailable(self, deployment_id: int) -> None:
        snap = self.get(deployment_id)
        snap.health = HealthState.UNAVAILABLE
        snap.last_seen_at = time.time()
        snap.last_error_at = time.time()

    def begin_request(self, deployment_id: int) -> None:
        snap = self.get(deployment_id)
        snap.active_requests += 1
        snap.last_seen_at = time.time()

    def end_request(
        self,
        deployment_id: int,
        *,
        success: bool,
        latency_ms: float,
    ) -> None:
        snap = self.get(deployment_id)
        snap.active_requests = max(0, snap.active_requests - 1)
        snap.last_seen_at = time.time()
        if success:
            snap.last_success_at = time.time()
            snap.health = HealthState.READY
            # simple EWMA-ish update for tests
            if snap.p50_ms is None:
                snap.p50_ms = latency_ms
            else:
                snap.p50_ms = 0.7 * snap.p50_ms + 0.3 * latency_ms
            if snap.p95_ms is None:
                snap.p95_ms = latency_ms
            else:
                snap.p95_ms = max(snap.p95_ms * 0.9, latency_ms)
        else:
            snap.last_error_at = time.time()

    def set_circuit(self, deployment_id: int, state: CircuitState) -> None:
        self.get(deployment_id).circuit_state = state
