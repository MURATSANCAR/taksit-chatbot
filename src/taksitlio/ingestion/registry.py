"""Adapter registry keyed by opaque adapter_code (ADR-010)."""

from __future__ import annotations

from typing import Callable

from taksitlio.ingestion.protocol import MerchantProductSourceAdapter

AdapterFactory = Callable[[], MerchantProductSourceAdapter]


class AdapterRegistry:
    """Maps ``adapter_code`` → factory. No merchant-name hardcoding."""

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, adapter_code: str, factory: AdapterFactory) -> None:
        code = (adapter_code or "").strip()
        if not code:
            raise ValueError("adapter_code required")
        if code in self._factories:
            raise ValueError(f"adapter already registered: {code}")
        self._factories[code] = factory

    def get(self, adapter_code: str) -> MerchantProductSourceAdapter:
        factory = self._factories.get(adapter_code)
        if factory is None:
            raise KeyError(f"unknown adapter_code: {adapter_code}")
        return factory()

    def known_codes(self) -> frozenset[str]:
        return frozenset(self._factories)


__all__ = ["AdapterFactory", "AdapterRegistry"]
