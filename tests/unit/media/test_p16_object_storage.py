"""P16 — object storage config, local CDN mount, admin media health."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.media.config import (
    ObjectStorageConfig,
    describe_object_storage,
    load_object_storage_config,
    probe_object_storage,
)
from taksitlio.media.s3_storage import S3CompatibleObjectStorage, build_object_storage_from_env
from taksitlio.media.storage import LocalObjectStorage


def test_load_local_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("MEDIA_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CDN_BASE_URL", "http://localhost:8000/cdn")
    cfg = load_object_storage_config()
    assert cfg.backend == "local"
    assert cfg.media_root == str(tmp_path)
    assert not cfg.is_placeholder_cdn
    cfg.validate()


def test_s3_requires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("CDN_BASE_URL", "https://cdn.real.example")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    cfg = load_object_storage_config()
    with pytest.raises(ValueError, match="S3_BUCKET"):
        cfg.validate()


def test_strict_cdn_rejects_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = ObjectStorageConfig(
        backend="s3",
        cdn_base_url="https://cdn.example.test",
        bucket="b1",
    )
    with pytest.raises(ValueError, match="placeholder"):
        cfg.validate(strict=True)


def test_s3_cdn_url_includes_prefix() -> None:
    class _Client:
        def put_object(self, **kwargs):  # type: ignore[no-untyped-def]
            return None

        def head_bucket(self, Bucket: str) -> None:
            assert Bucket == "media"

    storage = S3CompatibleObjectStorage(
        bucket="media",
        cdn_base_url="https://cdn.test",
        prefix="taksitlio",
        client=_Client(),
    )
    key = storage.put("media/original/ab/x.bin", b"hi", content_type="application/octet-stream")
    assert key == "taksitlio/media/original/ab/x.bin"
    assert storage.cdn_url_for(key) == "https://cdn.test/taksitlio/media/original/ab/x.bin"
    cfg = ObjectStorageConfig(
        backend="s3",
        cdn_base_url="https://cdn.test",
        bucket="media",
        prefix="taksitlio",
    )
    status = probe_object_storage(storage, config=cfg)
    assert status.ready is True
    assert status.detail == "head_bucket ok"


def test_describe_local(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path, cdn_base_url="http://localhost:8000/cdn")
    st = describe_object_storage(
        storage,
        config=ObjectStorageConfig(
            backend="local",
            cdn_base_url="http://localhost:8000/cdn",
            media_root=str(tmp_path),
        ),
    )
    assert st.ready is True
    assert st.local_cdn_mount is True


@pytest.mark.asyncio
async def test_admin_media_and_local_cdn_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("MEDIA_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CDN_BASE_URL", "http://test/cdn")

    from taksitlio.api.app import create_app
    from taksitlio.app.container import build_in_memory_container

    container = build_in_memory_container()
    # Ensure container storage points at tmp (factory may have run earlier)
    container.extras["media_storage"] = build_object_storage_from_env()
    app = create_app(container=container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rel = "media/smoke.txt"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")

        cdn = await client.get(f"/cdn/{rel}")
        assert cdn.status_code == 200
        assert cdn.text == "ok"

        admin = await client.get("/v1/admin/media/storage")
        assert admin.status_code == 200
        body = admin.json()
        assert body["backend"] == "local"
        assert body["ready"] is True

        ready = await client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["media_storage"]["backend"] == "local"
