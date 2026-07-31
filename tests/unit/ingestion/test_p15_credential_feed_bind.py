"""P15 — credential_ref resolve + authenticated generic feed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taksitlio.ingestion.binding import SourceBinding, instantiate_adapter
from taksitlio.secrets.resolve import (
    CredentialResolveError,
    http_headers_from_credential_ref,
    resolve_credential_ref,
)


def test_resolve_env_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_TOKEN", "tok-abc")
    resolved = resolve_credential_ref("env://FEED_TOKEN")
    assert resolved is not None
    assert resolved.headers["Authorization"] == "Bearer tok-abc"


def test_resolve_header_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k-1")
    resolved = resolve_credential_ref("header:X-Api-Key:env://API_KEY")
    assert resolved is not None
    assert resolved.headers == {"X-Api-Key": "k-1"}


def test_resolve_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_FEED_TOKEN", raising=False)
    with pytest.raises(CredentialResolveError, match="not set"):
        resolve_credential_ref("env://MISSING_FEED_TOKEN")


def test_unsupported_scheme() -> None:
    with pytest.raises(CredentialResolveError, match="unsupported"):
        resolve_credential_ref("secret://vault/path")


@pytest.mark.asyncio
async def test_http_feed_uses_resolved_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FEED_TOKEN", "tok-xyz")
    captured: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "products": [
                    {
                        "id": "SKU-9",
                        "name": "Fixture Phone",
                        "price": 9999,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                    }
                ]
            }

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: dict | None = None) -> _Resp:
            captured["url"] = url
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(
        "taksitlio.ingestion.adapters.generic_json_feed.httpx.AsyncClient",
        _Client,
    )
    binding = SourceBinding(
        source_code="src-http",
        adapter_code="generic.json_feed.v1",
        merchant_id="42",
        credential_ref="bearer:env://FEED_TOKEN",
        config={"feed_url": "https://feeds.example.test/products.json"},
    )
    adapter = instantiate_adapter(binding)
    refs = [r async for r in adapter.discover_products()]
    assert len(refs) == 1
    assert captured["url"] == "https://feeds.example.test/products.json"
    assert captured["headers"]["Authorization"] == "Bearer tok-xyz"
    assert http_headers_from_credential_ref(None) == {}


@pytest.mark.asyncio
async def test_admin_merchant_then_dry_run(tmp_path: Path) -> None:
    from httpx import ASGITransport, AsyncClient

    from taksitlio.api.app import create_app
    from taksitlio.app.container import build_in_memory_container

    feed = tmp_path / "feed.json"
    feed.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "SKU-A",
                        "name": "Fixture Laptop",
                        "price": 12000,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                        "image_url": "https://cdn.example.test/a.jpg",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    container = build_in_memory_container()
    app = create_app(container=container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/admin/merchants",
            json={
                "merchant_code": "ops-merchant-001",
                "display_name": "Operator Merchant Label",
            },
        )
        assert created.status_code == 200, created.text
        mid = created.json()["id"]
        assert mid >= 1

        listed = await client.get("/v1/admin/merchants")
        assert listed.status_code == 200
        assert any(m["merchant_code"] == "ops-merchant-001" for m in listed.json()["merchants"])

        dry = await client.post(
            "/v1/admin/ingestion/dry-run",
            json={
                "source_code": "src-ops-1",
                "adapter_code": "generic.json_feed.v1",
                "merchant_id": str(mid),
                "config": {"feed_path": str(feed)},
                "limit": 5,
            },
        )
        assert dry.status_code == 200, dry.text
        assert dry.json()["discovered"] == 1
        assert dry.json()["succeeded"] == 1
