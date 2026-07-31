"""Live dependency probes — typed failures, no silent fallback (ADR-009)."""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from taksitlio.runtime_verification.dependencies import (
    DependencyCode,
    DependencyProbeResult,
    RuntimeDependencyReport,
)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def probe_redis(*, url: Optional[str] = None) -> DependencyProbeResult:
    """Ping Redis. Unavailable → REDIS_UNAVAILABLE (never in-memory success)."""

    redis_url = url or _env("REDIS_URL")
    if not redis_url:
        return DependencyProbeResult(
            code=DependencyCode.REDIS_UNAVAILABLE,
            available=False,
            measured=True,
            detail="REDIS_URL not set",
        )
    try:
        import redis  # type: ignore
    except ImportError as exc:
        return DependencyProbeResult(
            code=DependencyCode.REDIS_UNAVAILABLE,
            available=False,
            measured=True,
            detail=f"redis package missing: {exc}",
        )

    try:
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2.0)
        pong = client.ping()
        client.close()
        if not pong:
            return DependencyProbeResult(
                code=DependencyCode.REDIS_UNAVAILABLE,
                available=False,
                measured=True,
                detail="PING returned falsy",
                metadata={"redis_url_host": urlparse(redis_url).hostname},
            )
        return DependencyProbeResult(
            code=None,
            available=True,
            measured=True,
            detail="PING ok",
            metadata={"redis_url_host": urlparse(redis_url).hostname},
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyProbeResult(
            code=DependencyCode.REDIS_UNAVAILABLE,
            available=False,
            measured=True,
            detail=str(exc),
            metadata={"redis_url_host": urlparse(redis_url).hostname},
        )


def probe_postgres(*, url: Optional[str] = None) -> DependencyProbeResult:
    pg_url = url or _env("PGVECTOR_URL") or _env("DATABASE_URL")
    if not pg_url:
        return DependencyProbeResult(
            code=DependencyCode.POSTGRES_UNAVAILABLE,
            available=False,
            measured=True,
            detail="PGVECTOR_URL/DATABASE_URL not set",
        )
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        return DependencyProbeResult(
            code=DependencyCode.POSTGRES_UNAVAILABLE,
            available=False,
            measured=True,
            detail=f"psycopg missing: {exc}",
        )
    try:
        with psycopg.connect(pg_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return DependencyProbeResult(
            code=None,
            available=True,
            measured=True,
            detail="SELECT 1 ok",
            metadata={"host": urlparse(pg_url).hostname},
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyProbeResult(
            code=DependencyCode.POSTGRES_UNAVAILABLE,
            available=False,
            measured=True,
            detail=str(exc),
            metadata={"host": urlparse(pg_url).hostname if pg_url else None},
        )


def probe_pgvector(*, url: Optional[str] = None) -> DependencyProbeResult:
    pg = probe_postgres(url=url)
    if not pg.available:
        return DependencyProbeResult(
            code=DependencyCode.POSTGRES_UNAVAILABLE,
            available=False,
            measured=True,
            detail=pg.detail,
            metadata=dict(pg.metadata),
        )
    pg_url = url or _env("PGVECTOR_URL") or _env("DATABASE_URL")
    try:
        import psycopg  # type: ignore

        with psycopg.connect(pg_url, connect_timeout=3, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                row = cur.fetchone()
                if not row:
                    return DependencyProbeResult(
                        code=DependencyCode.PGVECTOR_EXTENSION_UNAVAILABLE,
                        available=False,
                        measured=True,
                        detail="vector extension not present after CREATE",
                    )
                return DependencyProbeResult(
                    code=None,
                    available=True,
                    measured=True,
                    detail=f"vector extension {row[0]}",
                    metadata={"extversion": row[0]},
                )
    except Exception as exc:  # noqa: BLE001
        return DependencyProbeResult(
            code=DependencyCode.PGVECTOR_EXTENSION_UNAVAILABLE,
            available=False,
            measured=True,
            detail=str(exc),
        )


def probe_fast_deployment(
    *,
    base_url: Optional[str] = None,
    health_path: str = "/health",
) -> DependencyProbeResult:
    """HTTP health against env-configured FAST endpoint — no stub success."""

    url = (base_url or _env("FAST_PROVIDER_BASE_URL") or _env("POC_FAST_BASE_URL")).rstrip(
        "/"
    )
    if not url:
        return DependencyProbeResult(
            code=DependencyCode.FAST_DEPLOYMENT_UNAVAILABLE,
            available=False,
            measured=True,
            detail="FAST_PROVIDER_BASE_URL not set — refusing silent stub",
        )
    try:
        import httpx
    except ImportError as exc:
        return DependencyProbeResult(
            code=DependencyCode.FAST_DEPLOYMENT_UNAVAILABLE,
            available=False,
            measured=True,
            detail=f"httpx missing: {exc}",
        )
    health = f"{url}{health_path if health_path.startswith('/') else '/' + health_path}"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(health)
            if resp.status_code >= 400:
                return DependencyProbeResult(
                    code=DependencyCode.FAST_RUNTIME_UNHEALTHY,
                    available=False,
                    measured=True,
                    detail=f"health HTTP {resp.status_code}",
                    metadata={"health_url_host": urlparse(url).hostname},
                )
        return DependencyProbeResult(
            code=None,
            available=True,
            measured=True,
            detail="FAST health ok",
            metadata={"health_url_host": urlparse(url).hostname},
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyProbeResult(
            code=DependencyCode.FAST_DEPLOYMENT_UNAVAILABLE,
            available=False,
            measured=True,
            detail=str(exc),
            metadata={"health_url_host": urlparse(url).hostname},
        )


def probe_embedding_deployment(
    *,
    base_url: Optional[str] = None,
    health_path: str = "/health",
) -> DependencyProbeResult:
    url = (
        base_url
        or _env("EMBEDDING_PROVIDER_BASE_URL")
        or _env("POC_EMBEDDING_BASE_URL")
    ).rstrip("/")
    if not url:
        return DependencyProbeResult(
            code=DependencyCode.EMBEDDING_DEPLOYMENT_UNAVAILABLE,
            available=False,
            measured=True,
            detail="EMBEDDING_PROVIDER_BASE_URL not set — refusing lexical fallback",
        )
    try:
        import httpx
    except ImportError as exc:
        return DependencyProbeResult(
            code=DependencyCode.EMBEDDING_DEPLOYMENT_UNAVAILABLE,
            available=False,
            measured=True,
            detail=f"httpx missing: {exc}",
        )
    health = f"{url}{health_path if health_path.startswith('/') else '/' + health_path}"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(health)
            if resp.status_code >= 400:
                return DependencyProbeResult(
                    code=DependencyCode.EMBEDDING_RUNTIME_UNHEALTHY,
                    available=False,
                    measured=True,
                    detail=f"health HTTP {resp.status_code}",
                    metadata={"health_url_host": urlparse(url).hostname},
                )
        return DependencyProbeResult(
            code=None,
            available=True,
            measured=True,
            detail="embedding health ok",
            metadata={"health_url_host": urlparse(url).hostname},
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyProbeResult(
            code=DependencyCode.EMBEDDING_DEPLOYMENT_UNAVAILABLE,
            available=False,
            measured=True,
            detail=str(exc),
            metadata={"health_url_host": urlparse(url).hostname},
        )


def probe_all_dependencies(
    *,
    redis_url: Optional[str] = None,
    postgres_url: Optional[str] = None,
    fast_base_url: Optional[str] = None,
    embedding_base_url: Optional[str] = None,
) -> RuntimeDependencyReport:
    redis = probe_redis(url=redis_url)
    postgres = probe_postgres(url=postgres_url)
    pgvector = (
        probe_pgvector(url=postgres_url)
        if postgres.available
        else DependencyProbeResult(
            code=DependencyCode.PGVECTOR_EXTENSION_UNAVAILABLE,
            available=False,
            measured=True,
            detail="postgres unavailable — pgvector not probed",
        )
    )
    return RuntimeDependencyReport(
        redis=redis,
        postgres=postgres,
        pgvector=pgvector,
        fast=probe_fast_deployment(base_url=fast_base_url),
        embedding=probe_embedding_deployment(base_url=embedding_base_url),
    )


def unmeasured_report(*, detail: str = "not probed") -> RuntimeDependencyReport:
    """Placeholder report when probes have not run — all measured=False."""

    def _u(code: DependencyCode) -> DependencyProbeResult:
        return DependencyProbeResult(
            code=code, available=False, measured=False, detail=detail
        )

    return RuntimeDependencyReport(
        redis=_u(DependencyCode.REDIS_UNAVAILABLE),
        postgres=_u(DependencyCode.POSTGRES_UNAVAILABLE),
        pgvector=_u(DependencyCode.PGVECTOR_EXTENSION_UNAVAILABLE),
        fast=_u(DependencyCode.FAST_DEPLOYMENT_UNAVAILABLE),
        embedding=_u(DependencyCode.EMBEDDING_DEPLOYMENT_UNAVAILABLE),
    )


def evidence_metadata_from_env() -> Mapping[str, Any]:
    """Hardware / env metadata for reports — never includes secrets or model vendor slugs."""

    import platform
    import sys

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "has_fast_base_url": bool(_env("FAST_PROVIDER_BASE_URL") or _env("POC_FAST_BASE_URL")),
        "has_embedding_base_url": bool(
            _env("EMBEDDING_PROVIDER_BASE_URL") or _env("POC_EMBEDDING_BASE_URL")
        ),
        "has_redis_url": bool(_env("REDIS_URL")),
        "has_pgvector_url": bool(_env("PGVECTOR_URL") or _env("DATABASE_URL")),
    }
