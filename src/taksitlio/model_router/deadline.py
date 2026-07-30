"""Absolute request deadline helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Deadline:
    """Monotonic deadline for a single understanding request."""

    started_at: float
    total_budget_ms: int

    @classmethod
    def from_budget_ms(cls, total_budget_ms: int) -> "Deadline":
        return cls(started_at=time.monotonic(), total_budget_ms=max(0, int(total_budget_ms)))

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

    def remaining_ms(self) -> float:
        return float(self.total_budget_ms) - self.elapsed_ms()

    def is_exhausted(self, *, min_remaining_ms: float = 0.0) -> bool:
        return self.remaining_ms() <= min_remaining_ms

    def clamp_timeout_ms(self, desired_ms: int, *, min_remaining_ms: float = 0.0) -> int:
        remaining = self.remaining_ms()
        if remaining <= min_remaining_ms:
            return 0
        return max(1, int(min(float(desired_ms), remaining)))
