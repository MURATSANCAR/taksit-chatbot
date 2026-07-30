"""Lightweight observability hooks for the semantic matcher."""

from __future__ import annotations

from typing import Any, Protocol


class MatcherMetricsHook(Protocol):
    def incr(self, name: str, value: int = 1, **tags: Any) -> None: ...

    def observe(self, name: str, value: float, **tags: Any) -> None: ...


class NoOpMatcherMetricsHook:
    def incr(self, name: str, value: int = 1, **tags: Any) -> None:
        return None

    def observe(self, name: str, value: float, **tags: Any) -> None:
        return None


class InMemoryMatcherMetricsHook:
    def __init__(self) -> None:
        self.counters: dict[tuple, int] = {}
        self.observations: dict[tuple, list[float]] = {}

    def _key(self, name: str, tags: dict) -> tuple:
        return (name,) + tuple(sorted(tags.items()))

    def incr(self, name: str, value: int = 1, **tags: Any) -> None:
        self.counters[self._key(name, tags)] = (
            self.counters.get(self._key(name, tags), 0) + value
        )

    def observe(self, name: str, value: float, **tags: Any) -> None:
        self.observations.setdefault(self._key(name, tags), []).append(value)


__all__ = [
    "InMemoryMatcherMetricsHook",
    "MatcherMetricsHook",
    "NoOpMatcherMetricsHook",
]
