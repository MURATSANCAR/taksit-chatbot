"""Operator source binding — opaque adapter_code only (ADR-010).

Never embeds merchant display names. Credentials via credential_ref.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from taksitlio.ingestion.adapters.generic_json_feed import (
    ADAPTER_CODE as GENERIC_JSON_FEED,
    GenericJsonFeedAdapter,
)
from taksitlio.ingestion.protocol import MerchantProductSourceAdapter
from taksitlio.ingestion.registry import AdapterRegistry


@dataclass(frozen=True)
class SourceBinding:
    """Binds a catalog source row to a registered adapter factory inputs."""

    source_code: str
    adapter_code: str
    merchant_id: str  # opaque id, not a display name
    credential_ref: Optional[str] = None
    config: Mapping[str, Any] = field(default_factory=dict)


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()

    def _generic_factory() -> MerchantProductSourceAdapter:
        # Placeholder instance; real binds use instantiate_adapter with config.
        raise RuntimeError(
            "generic.json_feed.v1 requires instantiate_adapter(binding=...)"
        )

    registry.register(GENERIC_JSON_FEED, _generic_factory)
    return registry


def instantiate_adapter(
    binding: SourceBinding,
    *,
    registry: Optional[AdapterRegistry] = None,
) -> MerchantProductSourceAdapter:
    """Create adapter from binding config. Raises KeyError if adapter unknown."""

    reg = registry or build_default_registry()
    if binding.adapter_code not in reg.known_codes():
        raise KeyError(f"unknown adapter_code: {binding.adapter_code}")

    cfg = dict(binding.config or {})
    if binding.adapter_code == GENERIC_JSON_FEED:
        feed_url = cfg.get("feed_url")
        feed_path = cfg.get("feed_path")
        timeout = float(cfg.get("timeout_seconds", 30.0))
        source_ref = cfg.get("source_reference") or binding.source_code
        # credential_ref is resolved by ops/secrets layer — never inline secrets.
        if binding.credential_ref and "authorization" in cfg:
            raise ValueError(
                "inline authorization forbidden when credential_ref is set"
            )
        return GenericJsonFeedAdapter(
            feed_url=feed_url,
            feed_path=feed_path,
            timeout_seconds=timeout,
            source_reference=str(source_ref),
        )

    # Future adapters: factory(binding) pattern; keep registry check above.
    return reg.get(binding.adapter_code)


__all__ = [
    "SourceBinding",
    "build_default_registry",
    "instantiate_adapter",
]
