"""Async Postgres pool helpers."""

from __future__ import annotations

from typing import Any

import asyncpg


async def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=database_url,
        min_size=min_size,
        max_size=max_size,
        command_timeout=30,
    )


async def fetch_jsonb(row: asyncpg.Record, key: str) -> dict[str, Any]:
    value = row[key]
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return dict(value)
