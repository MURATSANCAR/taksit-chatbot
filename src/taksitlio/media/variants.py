"""Variant planning / optional WebP encoding (ADR-010 §39)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional, Sequence

VARIANT_WIDTHS: tuple[int, ...] = (320, 640, 1200)


@dataclass(frozen=True)
class MediaVariantPlan:
    variant_code: str
    width: int
    height: int
    mime_type: str
    data: bytes


def plan_variant_widths(
    source_width: int,
    *,
    widths: Sequence[int] = VARIANT_WIDTHS,
) -> tuple[int, ...]:
    return tuple(w for w in widths if w <= source_width) or (min(widths),)


def encode_webp_variants(
    data: bytes,
    *,
    source_width: int,
    source_height: int,
) -> tuple[MediaVariantPlan, ...]:
    """Encode WebP variants when Pillow is available; else empty tuple."""

    try:
        from PIL import Image
    except ImportError:
        return ()

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            plans: list[MediaVariantPlan] = []
            for width in plan_variant_widths(source_width):
                ratio = width / max(source_width, 1)
                height = max(1, int(round(source_height * ratio)))
                resized = img.resize((width, height))
                buf = io.BytesIO()
                resized.save(buf, format="WEBP", quality=80, method=4)
                plans.append(
                    MediaVariantPlan(
                        variant_code=f"w{width}",
                        width=width,
                        height=height,
                        mime_type="image/webp",
                        data=buf.getvalue(),
                    )
                )
            return tuple(plans)
    except Exception:
        return ()


def chatbot_default_variant_code(*, prefer_width: int = 640) -> str:
    """First-card payload should use 320 or 640, not full original."""

    if prefer_width <= 320:
        return "w320"
    return "w640"


__all__ = [
    "MediaVariantPlan",
    "VARIANT_WIDTHS",
    "chatbot_default_variant_code",
    "encode_webp_variants",
    "plan_variant_widths",
]
