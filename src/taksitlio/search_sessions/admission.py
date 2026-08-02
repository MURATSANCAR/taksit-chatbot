"""Bounded admission / backpressure for search-session starts.

Policy thresholds are loaded from DB (public_overload_policy_versions) when
available; safe defaults apply until then. Never invents products under load.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class OverloadPolicy:
    maximum_inflight: int = 64
    maximum_queue_wait_ms: float = 2000.0
    retry_after_seconds: int = 2
    degraded_mode: str = "BUSY_REJECT"  # BUSY_REJECT | DEGRADED_EMPTY


_state = {
    "inflight": 0,
    "admitted": 0,
    "rejected": 0,
    "peak_inflight": 0,
}
_policy = OverloadPolicy()


def update_policy(raw: Optional[dict[str, Any]]) -> OverloadPolicy:
    global _policy
    if not isinstance(raw, dict):
        return _policy
    _policy = OverloadPolicy(
        maximum_inflight=int(raw.get("maximum_inflight") or raw.get("maximum_queue_depth") or 64),
        maximum_queue_wait_ms=float(raw.get("maximum_queue_wait_ms") or raw.get("maximum_queue_wait") or 2000),
        retry_after_seconds=int(raw.get("retry_after_seconds") or raw.get("retry_after") or 2),
        degraded_mode=str(raw.get("degraded_mode") or "BUSY_REJECT"),
    )
    return _policy


def current_policy() -> OverloadPolicy:
    return _policy


def stats() -> dict[str, Any]:
    return {
        **_state,
        "maximum_inflight": _policy.maximum_inflight,
        "retry_after_seconds": _policy.retry_after_seconds,
        "degraded_mode": _policy.degraded_mode,
    }


class AdmissionTicket:
    __slots__ = ("admitted_at", "released")

    def __init__(self) -> None:
        self.admitted_at = time.perf_counter()
        self.released = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        _state["inflight"] = max(0, int(_state["inflight"]) - 1)


def try_admit() -> Optional[AdmissionTicket]:
    """Non-blocking admission. Returns None when overloaded."""

    if int(_state["inflight"]) >= int(_policy.maximum_inflight):
        _state["rejected"] = int(_state["rejected"]) + 1
        return None
    _state["inflight"] = int(_state["inflight"]) + 1
    _state["admitted"] = int(_state["admitted"]) + 1
    _state["peak_inflight"] = max(int(_state["peak_inflight"]), int(_state["inflight"]))
    return AdmissionTicket()
