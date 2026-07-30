"""Postgres-backed AI model / route / prompt repositories."""

from __future__ import annotations

from typing import Any

import asyncpg

from taksitlio.model_gateway.gateway import ModelProfile
from taksitlio.model_gateway.repository import InMemoryProfileRepository
from taksitlio.model_router.router import (
    ConfidencePolicy,
    TaskRoute,
    TimeoutPolicy,
)


def _bool_or(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _profile_from_row(row: asyncpg.Record) -> ModelProfile:
    configuration = row["configuration"] or {}
    if not isinstance(configuration, dict):
        configuration = dict(configuration)
    return ModelProfile(
        id=int(row["id"]),
        profile_code=row["profile_code"],
        display_name=row["display_name"],
        provider_type=row["provider_type"],
        endpoint_url=row["endpoint_url"],
        model_reference=row["model_reference"],
        task_type=row["task_type"],
        context_limit=int(row["context_limit"]),
        max_output_tokens=int(row["max_output_tokens"]),
        temperature=float(row["temperature"]),
        timeout_ms=int(row["timeout_ms"]),
        parallel_slots=int(row["parallel_slots"]),
        status=row["status"],
        configuration=configuration,
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
                rows = await conn.fetch(
                    "SELECT * FROM ai_model_profiles ORDER BY id"
                )
        return [_profile_from_row(r) for r in rows]

    async def update_profile(
        self,
        profile_code: str,
        *,
        endpoint_url: str | None = None,
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
            ("endpoint_url", endpoint_url),
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


class AsyncProfileAdapter:
    """Sync Protocol adapter that caches profiles for ModelGateway."""

    def __init__(self, repo: PostgresProfileRepository) -> None:
        self._repo = repo
        self._cache: dict[str, ModelProfile] = {}
        self._by_id: dict[int, ModelProfile] = {}

    async def refresh(self) -> None:
        profiles = await self._repo.list_profiles()
        self._cache = {p.profile_code: p for p in profiles}
        self._by_id = {p.id: p for p in profiles}

    def get_by_code(self, profile_code: str) -> ModelProfile:
        try:
            return self._cache[profile_code]
        except KeyError as exc:
            raise KeyError(f"Unknown model profile_code: {profile_code}") from exc

    def get_by_id(self, profile_id: int) -> ModelProfile:
        try:
            return self._by_id[profile_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model profile id: {profile_id}") from exc

    def as_in_memory(self) -> InMemoryProfileRepository:
        return InMemoryProfileRepository(self._cache.values())


class PostgresTaskRouteRepository:
    def __init__(self, pool: asyncpg.Pool, profiles: AsyncProfileAdapter) -> None:
        self._pool = pool
        self._profiles = profiles
        self._cache: dict[str, TaskRoute] = {}

    async def refresh(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    r.task_code,
                    r.primary_model_profile_id,
                    r.fallback_model_profile_id,
                    cp.policy_code AS conf_code,
                    cp.minimum_confidence,
                    cp.maximum_category_score_gap_for_clarification,
                    cp.fallback_on_invalid_schema,
                    cp.fallback_on_conflict,
                    cp.fallback_on_multiple_needs,
                    cp.fallback_on_budget_confusion,
                    cp.fallback_on_low_confidence,
                    cp.prefer_clarification_when_ambiguous,
                    tp.policy_code AS timeout_code,
                    tp.primary_timeout_ms,
                    tp.fallback_timeout_ms,
                    tp.total_budget_ms,
                    tp.retry_same_model
                FROM ai_task_routes r
                LEFT JOIN ai_confidence_policies cp ON cp.id = r.confidence_policy_id
                LEFT JOIN ai_timeout_policies tp ON tp.id = r.timeout_policy_id
                WHERE r.status = 'ACTIVE'
                """
            )
        routes: dict[str, TaskRoute] = {}
        for row in rows:
            primary = self._profiles.get_by_id(int(row["primary_model_profile_id"]))
            fallback = None
            if row["fallback_model_profile_id"] is not None:
                fallback = self._profiles.get_by_id(int(row["fallback_model_profile_id"]))
            conf = ConfidencePolicy(
                policy_code=row["conf_code"] or "DEFAULT",
                minimum_confidence=float(row["minimum_confidence"] or 0.78),
                maximum_category_score_gap_for_clarification=float(
                    row["maximum_category_score_gap_for_clarification"] or 0.08
                ),
                fallback_on_invalid_schema=_bool_or(
                    row["fallback_on_invalid_schema"], True
                ),
                fallback_on_conflict=_bool_or(row["fallback_on_conflict"], True),
                fallback_on_multiple_needs=_bool_or(
                    row["fallback_on_multiple_needs"], True
                ),
                fallback_on_budget_confusion=_bool_or(
                    row["fallback_on_budget_confusion"], True
                ),
                fallback_on_low_confidence=_bool_or(
                    row["fallback_on_low_confidence"], True
                ),
                prefer_clarification_when_ambiguous=_bool_or(
                    row["prefer_clarification_when_ambiguous"], True
                ),
            )
            timeout = TimeoutPolicy(
                policy_code=row["timeout_code"] or "DEFAULT",
                primary_timeout_ms=int(row["primary_timeout_ms"] or 3000),
                fallback_timeout_ms=int(row["fallback_timeout_ms"] or 8000),
                total_budget_ms=int(row["total_budget_ms"] or 10000),
                retry_same_model=_bool_or(row["retry_same_model"], False),
            )
            routes[row["task_code"]] = TaskRoute(
                task_code=row["task_code"],
                primary=primary,
                fallback=fallback,
                confidence_policy=conf,
                timeout_policy=timeout,
            )
        self._cache = routes

    def get_route(self, task_code: str) -> TaskRoute:
        try:
            return self._cache[task_code]
        except KeyError as exc:
            raise KeyError(f"Unknown or inactive task_code: {task_code}") from exc


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
