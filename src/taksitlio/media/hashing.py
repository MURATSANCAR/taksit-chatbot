"""Image sniffing, hashing, and lightweight perceptual hash."""

from __future__ import annotations

import hashlib
import io
from typing import Optional

# Magic signatures (prefix)
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # refined below
)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_mime(data: bytes) -> Optional[str]:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for sig, mime in _SIGNATURES:
        if mime == "image/webp":
            continue
        if data.startswith(sig):
            return mime
    return None


def decode_dimensions(data: bytes) -> tuple[Optional[int], Optional[int], bool]:
    """Return (width, height, decode_ok). Uses Pillow when available."""

    try:
        from PIL import Image
    except ImportError:
        return None, None, False

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            w, h = img.size
            return int(w), int(h), True
    except Exception:
        return None, None, False


def perceptual_hash_hex(data: bytes) -> Optional[str]:
    """8x8 average hash → 16 hex chars. None if Pillow missing/decode fails."""

    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(data)) as img:
            gray = img.convert("L").resize((8, 8))
            pixels = list(gray.getdata())
    except Exception:
        return None

    avg = sum(pixels) / max(len(pixels), 1)
    bits = 0
    for i, px in enumerate(pixels):
        if px >= avg:
            bits |= 1 << i
    return f"{bits:016x}"


def hamming_distance_hex(a: str, b: str) -> int:
    x = int(a, 16) ^ int(b, 16)
    return x.bit_count() if hasattr(x, "bit_count") else bin(x).count("1")


__all__ = [
    "decode_dimensions",
    "hamming_distance_hex",
    "perceptual_hash_hex",
    "sha256_hex",
    "sniff_mime",
]
