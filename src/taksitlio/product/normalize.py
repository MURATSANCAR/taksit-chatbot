"""Turkish-aware display name normalization for product search keys."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")


def normalize_display_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().strip()
    # Drop combining marks (e.g. İ → i + combining dot).
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = (
        text.replace("ı", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
    )
    return _WS.sub(" ", text)


__all__ = ["normalize_display_name"]
