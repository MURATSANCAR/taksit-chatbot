"""Operator source binding — opaque adapter_code only (ADR-010).

Never embeds merchant display names. Credentials via credential_ref.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from taksitlio.ingestion.adapters.generic_campaign_feed import (
    ADAPTER_CODE as GENERIC_CAMPAIGN_FEED,
    GenericCampaignFeedAdapter,
)
from taksitlio.ingestion.adapters.generic_json_feed import (
    ADAPTER_CODE as GENERIC_JSON_FEED,
    GenericJsonFeedAdapter,
)
from taksitlio.ingestion.protocol import MerchantProductSourceAdapter
from taksitlio.ingestion.registry import AdapterRegistry
from taksitlio.secrets.resolve import (
    CredentialResolveError,
    http_headers_from_credential_ref,
)


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

    def _campaign_factory() -> MerchantProductSourceAdapter:
        raise RuntimeError(
            "generic.campaign_feed.v1 requires instantiate_campaign_adapter(binding=...)"
        )

    registry.register(GENERIC_JSON_FEED, _generic_factory)
    registry.register(GENERIC_CAMPAIGN_FEED, _campaign_factory)  # type: ignore[arg-type]
    return registry


def instantiate_adapter(
    binding: SourceBinding,
    *,
    registry: Optional[AdapterRegistry] = None,
) -> MerchantProductSourceAdapter:
    """Create product adapter from binding config. Raises KeyError if adapter unknown."""

    reg = registry or build_default_registry()
    if binding.adapter_code not in reg.known_codes():
        raise KeyError(f"unknown adapter_code: {binding.adapter_code}")
    if binding.adapter_code == GENERIC_CAMPAIGN_FEED:
        raise ValueError(
            "use instantiate_campaign_adapter for generic.campaign_feed.v1"
        )

    cfg = dict(binding.config or {})
    if binding.adapter_code == GENERIC_JSON_FEED:
        feed_url = cfg.get("feed_url")
        feed_path = cfg.get("feed_path")
        timeout = float(cfg.get("timeout_seconds", 30.0))
        source_ref = cfg.get("source_reference") or binding.source_code
        # Never allow inline secrets alongside or instead of credential_ref.
        for forbidden in ("authorization", "api_key", "token", "password"):
            if forbidden in cfg:
                raise ValueError(
                    f"inline {forbidden} forbidden; use credential_ref (env://…)"
                )
        try:
            headers = http_headers_from_credential_ref(binding.credential_ref)
        except CredentialResolveError as exc:
            raise ValueError(str(exc)) from exc
        return GenericJsonFeedAdapter(
            feed_url=feed_url,
            feed_path=feed_path,
            timeout_seconds=timeout,
            source_reference=str(source_ref),
            request_headers=headers,
        )

    # Future adapters: factory(binding) pattern; keep registry check above.
    return reg.get(binding.adapter_code)


def instantiate_campaign_adapter(
    binding: SourceBinding,
    *,
    registry: Optional[AdapterRegistry] = None,
) -> GenericCampaignFeedAdapter:
    """Create campaign feed adapter (opaque adapter_code only)."""

    reg = registry or build_default_registry()
    if binding.adapter_code not in reg.known_codes():
        raise KeyError(f"unknown adapter_code: {binding.adapter_code}")
    if binding.adapter_code != GENERIC_CAMPAIGN_FEED:
        raise ValueError(f"not a campaign adapter: {binding.adapter_code}")

    cfg = dict(binding.config or {})
    for forbidden in ("authorization", "api_key", "token", "password"):
        if forbidden in cfg:
            raise ValueError(
                f"inline {forbidden} forbidden; use credential_ref (env://…)"
            )
    try:
        headers = http_headers_from_credential_ref(binding.credential_ref)
    except CredentialResolveError as exc:
        raise ValueError(str(exc)) from exc
    return GenericCampaignFeedAdapter(
        feed_url=cfg.get("feed_url"),
        feed_path=cfg.get("feed_path"),
        timeout_seconds=float(cfg.get("timeout_seconds", 30.0)),
        source_reference=str(cfg.get("source_reference") or binding.source_code),
        request_headers=headers,
        default_institution_code=cfg.get("institution_code"),
    )


__all__ = [
    "SourceBinding",
    "build_default_registry",
    "instantiate_adapter",
    "instantiate_campaign_adapter",
]
