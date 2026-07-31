"""Object storage + CDN URL construction (no hotlink of source_url)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Persist bytes; return storage_key."""
        ...

    def cdn_url_for(self, key: str) -> str:
        """Public CDN URL for a stored key — never the merchant source URL."""
        ...


class LocalObjectStorage:
    """Filesystem-backed storage for tests / local POC."""

    def __init__(self, root: str | Path, *, cdn_base_url: str = "https://cdn.example.test") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cdn_base_url = cdn_base_url.rstrip("/")

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        _ = content_type
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def cdn_url_for(self, key: str) -> str:
        return f"{self.cdn_base_url}/{key.lstrip('/')}"

    def get(self, key: str) -> Optional[bytes]:
        path = self.root / key
        if not path.is_file():
            return None
        return path.read_bytes()


__all__ = ["LocalObjectStorage", "ObjectStorage"]
