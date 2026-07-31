"""ADR-010 P2 — media pipeline tests (no hotlink)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from taksitlio.media import (
    IMAGE_UNAVAILABLE,
    LocalObjectStorage,
    MediaStatus,
    evaluate_image_quality,
    ingest_image_bytes,
    select_primary_candidate,
)
from taksitlio.media.hashing import sha256_hex, sniff_mime
from taksitlio.media.variants import chatbot_default_variant_code, plan_variant_widths


def _png_bytes(width: int = 800, height: int = 800, color=(40, 120, 200)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_sniff_png_mime() -> None:
    data = _png_bytes()
    assert sniff_mime(data) == "image/png"


def test_quality_rejects_small_image() -> None:
    q = evaluate_image_quality(width=200, height=200, file_size=1000, decode_ok=True)
    assert q.acceptable_for_primary is False
    assert q.min_width_ok is False


def test_quality_accepts_primary_target() -> None:
    q = evaluate_image_quality(width=1000, height=1000, file_size=50_000, decode_ok=True)
    assert q.acceptable_for_primary is True
    assert q.preferred_width_ok is True


def test_ingest_stores_cdn_not_source(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "obj", cdn_base_url="https://cdn.taksitlio.test")
    source = "https://merchant.example/images/product-1.png"
    data = _png_bytes(1000, 1000)
    outcome = ingest_image_bytes(data, source_url=source, storage=storage)
    draft = outcome.draft
    assert draft.status is MediaStatus.READY
    assert draft.cdn_url is not None
    assert draft.cdn_url.startswith("https://cdn.taksitlio.test/")
    assert "merchant.example" not in draft.cdn_url
    assert draft.storage_key is not None
    assert storage.get(draft.storage_key) == data
    assert draft.variants  # webp variants encoded
    assert all("merchant.example" not in v["cdn_url"] for v in draft.variants)


def test_ingest_quarantines_garbage(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "obj")
    outcome = ingest_image_bytes(
        b"not-an-image",
        source_url="https://merchant.example/x.bin",
        storage=storage,
    )
    assert outcome.draft.status is MediaStatus.QUARANTINED


def test_duplicate_sha_skip(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "obj")
    data = _png_bytes(700, 700)
    digest = sha256_hex(data)
    outcome = ingest_image_bytes(
        data,
        source_url="https://merchant.example/a.png",
        storage=storage,
        known_sha256={digest},
    )
    assert outcome.skipped_duplicate_sha is True


def test_primary_selection_image_unavailable() -> None:
    sel = select_primary_candidate([])
    assert sel.status == IMAGE_UNAVAILABLE
    assert sel.asset is None


def test_primary_selection_picks_higher_quality(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "obj")
    weak = ingest_image_bytes(
        _png_bytes(650, 650, color=(10, 10, 10)),
        source_url="https://src/a.png",
        storage=storage,
    ).draft
    strong = ingest_image_bytes(
        _png_bytes(1200, 1200, color=(200, 200, 200)),
        source_url="https://src/b.png",
        storage=storage,
    ).draft
    sel = select_primary_candidate([weak, strong])
    assert sel.status == "READY"
    assert sel.asset is not None
    assert sel.asset.sha256 == strong.sha256


def test_chatbot_uses_small_variant_code() -> None:
    assert chatbot_default_variant_code(prefer_width=640) == "w640"
    assert chatbot_default_variant_code(prefer_width=320) == "w320"
    assert plan_variant_widths(900) == (320, 640)
