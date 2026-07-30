"""Postgres-backed AI profile / prompt repositories (deployments via route_repository)."""

from __future__ import annotations

from typing import Any

import asyncpg

from taksitlio.model_gateway.types import ModelProfile


def _profile_from_row(row: asyncpg.Record) -> ModelProfile:
    configuration = row["configuration"] or {}
    if not isinstance(configuration, dict):
        configuration = dict(configuration)
    return ModelProfile(
        id=int(row["id"]),
        profile_code=row["profile_code"],
        display_name=row["display_name"],
        provider_type=row["provider_type"],
        model_reference=row["model_reference"],
        task_type=row["task_type"],
        context_limit=int(row["context_limit"]),
        max_output_tokens=int(row["max_output_tokens"]),
        temperature=float(row["temperature"]),
        timeout_ms=int(row["timeout_ms"]),
        parallel_slots=int(row["parallel_slots"]),
        status=row["status"],
        configuration=configuration,
        endpoint_url=row["endpoint_url"],
    )


class PostgresProfileRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_by_code(self, profile_code: str) -> ModelProfile:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ai_model_profiles WHERE profile_code = $1",
                profile_code,
            )
        if row is None:
            raise KeyError(f"Unknown model profile_code: {profile_code}")
        return _profile_from_row(row)

    async def get_by_id(self, profile_id: int) -> ModelProfile:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ai_model_profiles WHERE id = $1",
                profile_id,
            )
        if row is None:
            raise KeyError(f"Unknown model profile id: {profile_id}")
        return _profile_from_row(row)

    async def list_profiles(self, *, status: str | None = None) -> list[ModelProfile]:
        async with self._pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT * FROM ai_model_profiles WHERE status = $1 ORDER BY id",
                    status,
                )
            else:
                rows = await conn.fetch("SELECT * FROM ai_model_profiles ORDER BY id")
        return [_profile_from_row(r) for r in rows]

    async def update_profile(
        self,
        profile_code: str,
        *,
        timeout_ms: int | None = None,
        parallel_slots: int | None = None,
        temperature: float | None = None,
        status: str | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ModelProfile:
        sets: list[str] = ["updated_at = NOW()"]
        args: list[Any] = []
        idx = 1
        for col, val in (
            ("timeout_ms", timeout_ms),
            ("parallel_slots", parallel_slots),
            ("temperature", temperature),
            ("status", status),
            ("configuration", configuration),
        ):
            if val is not None:
                sets.append(f"{col} = ${idx}")
                args.append(val)
                idx += 1
        args.append(profile_code)
        sql = (
            f"UPDATE ai_model_profiles SET {', '.join(sets)} "
            f"WHERE profile_code = ${idx} RETURNING *"
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
        if row is None:
            raise KeyError(f"Unknown model profile_code: {profile_code}")
        return _profile_from_row(row)


class PostgresPromptRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_active(self, prompt_code: str) -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT content FROM ai_prompt_versions
                WHERE prompt_code = $1 AND is_active = TRUE
                """,
                prompt_code,
            )
        if row is None:
            raise KeyError(f"No active prompt for code: {prompt_code}")
        return str(row["content"])

    async def list_versions(self, prompt_code: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, prompt_code, version, task_code, is_active, notes, created_at
                FROM ai_prompt_versions
                WHERE prompt_code = $1
                ORDER BY version DESC
                """,
                prompt_code,
            )
        return [dict(r) for r in rows]

    async def activate(self, prompt_code: str, version: int) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE ai_prompt_versions SET is_active = FALSE WHERE prompt_code = $1",
                    prompt_code,
                )
                result = await conn.execute(
                    """
                    UPDATE ai_prompt_versions SET is_active = TRUE
                    WHERE prompt_code = $1 AND version = $2
                    """,
                    prompt_code,
                    version,
                )
                if result.endswith("0"):
                    raise KeyError(f"Prompt {prompt_code} v{version} not found")
