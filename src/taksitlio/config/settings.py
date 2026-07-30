"""Infrastructure settings — never model/category/campaign content."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default if default is not None else "").strip()
    return value


@dataclass(frozen=True)
class InfraSettings:
    """Connection endpoints for infrastructure only."""

    database_url: str
    redis_url: str
    redis_key_prefix: str = "taksitlio"
    session_ttl_seconds: int = 86400
    http_timeout_seconds: float = 30.0
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    allow_in_memory: bool = False

    @classmethod
    def from_env(cls, *, allow_missing: bool = False) -> "InfraSettings":
        database_url = _env("DATABASE_URL")
        redis_url = _env("REDIS_URL")
        allow_in_memory = _env("ALLOW_IN_MEMORY", "false").lower() in {
            "1",
            "true",
            "yes",
        }

        if not allow_missing and not allow_in_memory:
            if not database_url:
                raise ValueError(
                    "DATABASE_URL is required (Postgres). Model endpoints stay in DB."
                )
            if not redis_url:
                raise ValueError("REDIS_URL is required (session state).")

        return cls(
            database_url=database_url or "postgresql://taksitlio:taksitlio@postgres:5432/taksitlio",
            redis_url=redis_url or "redis://redis:6379/0",
            redis_key_prefix=_env("REDIS_KEY_PREFIX", "taksitlio") or "taksitlio",
            session_ttl_seconds=int(_env("SESSION_TTL_SECONDS", "86400")),
            http_timeout_seconds=float(_env("HTTP_TIMEOUT_SECONDS", "30")),
            api_host=_env("API_HOST", "0.0.0.0") or "0.0.0.0",
            api_port=int(_env("API_PORT", "8000")),
            log_level=_env("LOG_LEVEL", "INFO") or "INFO",
            allow_in_memory=allow_in_memory,
        )
