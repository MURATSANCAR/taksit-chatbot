"""Postgres-backed route version + deployment repository."""

from __future__ import annotations

import json
from typing import Sequence

import asyncpg

from taksitlio.model_gateway.types import ModelDeployment, ModelProfile, ProviderConnection
from taksitlio.model_router.route_selector import RouteVersion
from taksitlio.model_router.router_types import ConfidencePolicy, TimeoutPolicy


class PostgresRouteVersionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._cache: list[RouteVersion] = []

    async def refresh(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    rv.id AS route_id,
                    rv.task_code,
                    rv.route_version,
                    rv.display_name,
                    rv.condition_expression,
                    rv.traffic_weight AS route_traffic_weight,
                    rv.priority AS route_priority,
                    rv.effective_from,
                    rv.effective_until,
                    rv.is_active,
                    cp.policy_code AS conf_code,
                    cp.minimum_system_confidence,
                    cp.minimum_confidence,
                    cp.maximum_category_score_gap_for_clarification,
                    cp.fallback_on_invalid_schema,
                    cp.fallback_on_low_confidence,
                    cp.prefer_clarification_when_ambiguous,
                    cp.clarify_on_session_conflict,
                    cp.clarify_on_multiple_needs,
                    tp.policy_code AS timeout_code,
                    tp.primary_timeout_ms,
                    tp.fallback_timeout_ms,
                    tp.total_budget_ms,
                    tp.min_fallback_remaining_ms,
                    tp.retry_same_model,
                    pd.id AS p_dep_id,
                    pd.deployment_code AS p_dep_code,
                    pd.runtime_alias AS p_alias,
                    pd.priority AS p_dep_priority,
                    pd.traffic_weight AS p_dep_traffic,
                    pd.max_parallel_requests AS p_dep_parallel,
                    pd.status AS p_dep_status,
                    pd.configuration AS p_dep_cfg,
                    pp.id AS p_prof_id,
                    pp.profile_code AS p_prof_code,
                    pp.display_name AS p_prof_name,
                    pp.provider_type AS p_prof_provider,
                    pp.endpoint_url AS p_prof_endpoint,
                    pp.model_reference AS p_prof_ref,
                    pp.task_type AS p_prof_task,
                    pp.context_limit AS p_prof_ctx,
                    pp.max_output_tokens AS p_prof_max_out,
                    pp.temperature AS p_prof_temp,
                    pp.timeout_ms AS p_prof_timeout,
                    pp.parallel_slots AS p_prof_slots,
                    pp.status AS p_prof_status,
                    pp.configuration AS p_prof_cfg,
                    pc.id AS p_conn_id,
                    pc.connection_code AS p_conn_code,
                    pc.provider_type AS p_conn_provider,
                    pc.base_url AS p_conn_base,
                    pc.credential_ref AS p_conn_cred,
                    pc.configuration AS p_conn_cfg,
                    pc.status AS p_conn_status,
                    fd.id AS f_dep_id,
                    fd.deployment_code AS f_dep_code,
                    fd.runtime_alias AS f_alias,
                    fd.priority AS f_dep_priority,
                    fd.traffic_weight AS f_dep_traffic,
                    fd.max_parallel_requests AS f_dep_parallel,
                    fd.status AS f_dep_status,
                    fd.configuration AS f_dep_cfg,
                    fp.id AS f_prof_id,
                    fp.profile_code AS f_prof_code,
                    fp.display_name AS f_prof_name,
                    fp.provider_type AS f_prof_provider,
                    fp.endpoint_url AS f_prof_endpoint,
                    fp.model_reference AS f_prof_ref,
                    fp.task_type AS f_prof_task,
                    fp.context_limit AS f_prof_ctx,
                    fp.max_output_tokens AS f_prof_max_out,
                    fp.temperature AS f_prof_temp,
                    fp.timeout_ms AS f_prof_timeout,
                    fp.parallel_slots AS f_prof_slots,
                    fp.status AS f_prof_status,
                    fp.configuration AS f_prof_cfg,
                    fc.id AS f_conn_id,
                    fc.connection_code AS f_conn_code,
                    fc.provider_type AS f_conn_provider,
                    fc.base_url AS f_conn_base,
                    fc.credential_ref AS f_conn_cred,
                    fc.configuration AS f_conn_cfg,
                    fc.status AS f_conn_status
                FROM ai_route_versions rv
                JOIN ai_confidence_policies cp ON cp.id = rv.confidence_policy_id
                JOIN ai_timeout_policies tp ON tp.id = rv.timeout_policy_id
                JOIN ai_model_deployments pd ON pd.id = rv.primary_deployment_id
                JOIN ai_model_profiles pp ON pp.id = pd.model_profile_id
                JOIN ai_provider_connections pc ON pc.id = pd.provider_connection_id
                LEFT JOIN ai_model_deployments fd ON fd.id = rv.fallback_deployment_id
                LEFT JOIN ai_model_profiles fp ON fp.id = fd.model_profile_id
                LEFT JOIN ai_provider_connections fc ON fc.id = fd.provider_connection_id
                WHERE rv.is_active = TRUE
                """
            )
        self._cache = [_row_to_route(r) for r in rows]

    def list_active(self, task_code: str) -> Sequence[RouteVersion]:
        return [r for r in self._cache if r.task_code == task_code and r.is_active]


def _cfg(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        import json

        parsed = json.loads(text)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return dict(value)


def _deployment(
    *,
    dep_id,
    dep_code,
    alias,
    priority,
    traffic,
    parallel,
    status,
    dep_cfg,
    prof_id,
    prof_code,
    prof_name,
    prof_provider,
    prof_endpoint,
    prof_ref,
    prof_task,
    prof_ctx,
    prof_max_out,
    prof_temp,
    prof_timeout,
    prof_slots,
    prof_status,
    prof_cfg,
    conn_id,
    conn_code,
    conn_provider,
    conn_base,
    conn_cred,
    conn_cfg,
    conn_status,
) -> ModelDeployment:
    profile = ModelProfile(
        id=int(prof_id),
        profile_code=prof_code,
        display_name=prof_name,
        provider_type=prof_provider,
        model_reference=prof_ref,
        task_type=prof_task,
        context_limit=int(prof_ctx),
        max_output_tokens=int(prof_max_out),
        temperature=float(prof_temp),
        timeout_ms=int(prof_timeout),
        parallel_slots=int(prof_slots),
        status=prof_status,
        configuration=_cfg(prof_cfg),
        endpoint_url=prof_endpoint,
    )
    connection = ProviderConnection(
        id=int(conn_id),
        connection_code=conn_code,
        provider_type=conn_provider,
        base_url=conn_base,
        credential_ref=conn_cred,
        configuration=_cfg(conn_cfg),
        status=conn_status,
    )
    return ModelDeployment(
        id=int(dep_id),
        deployment_code=dep_code,
        profile=profile,
        connection=connection,
        runtime_alias=alias,
        priority=int(priority),
        traffic_weight=float(traffic),
        max_parallel_requests=parallel,
        status=status,
        configuration=_cfg(dep_cfg),
    )


def _row_to_route(row: asyncpg.Record) -> RouteVersion:
    primary = _deployment(
        dep_id=row["p_dep_id"],
        dep_code=row["p_dep_code"],
        alias=row["p_alias"],
        priority=row["p_dep_priority"],
        traffic=row["p_dep_traffic"],
        parallel=row["p_dep_parallel"],
        status=row["p_dep_status"],
        dep_cfg=row["p_dep_cfg"],
        prof_id=row["p_prof_id"],
        prof_code=row["p_prof_code"],
        prof_name=row["p_prof_name"],
        prof_provider=row["p_prof_provider"],
        prof_endpoint=row["p_prof_endpoint"],
        prof_ref=row["p_prof_ref"],
        prof_task=row["p_prof_task"],
        prof_ctx=row["p_prof_ctx"],
        prof_max_out=row["p_prof_max_out"],
        prof_temp=row["p_prof_temp"],
        prof_timeout=row["p_prof_timeout"],
        prof_slots=row["p_prof_slots"],
        prof_status=row["p_prof_status"],
        prof_cfg=row["p_prof_cfg"],
        conn_id=row["p_conn_id"],
        conn_code=row["p_conn_code"],
        conn_provider=row["p_conn_provider"],
        conn_base=row["p_conn_base"],
        conn_cred=row["p_conn_cred"],
        conn_cfg=row["p_conn_cfg"],
        conn_status=row["p_conn_status"],
    )
    fallback = None
    if row["f_dep_id"] is not None:
        fallback = _deployment(
            dep_id=row["f_dep_id"],
            dep_code=row["f_dep_code"],
            alias=row["f_alias"],
            priority=row["f_dep_priority"],
            traffic=row["f_dep_traffic"],
            parallel=row["f_dep_parallel"],
            status=row["f_dep_status"],
            dep_cfg=row["f_dep_cfg"],
            prof_id=row["f_prof_id"],
            prof_code=row["f_prof_code"],
            prof_name=row["f_prof_name"],
            prof_provider=row["f_prof_provider"],
            prof_endpoint=row["f_prof_endpoint"],
            prof_ref=row["f_prof_ref"],
            prof_task=row["f_prof_task"],
            prof_ctx=row["f_prof_ctx"],
            prof_max_out=row["f_prof_max_out"],
            prof_temp=row["f_prof_temp"],
            prof_timeout=row["f_prof_timeout"],
            prof_slots=row["f_prof_slots"],
            prof_status=row["f_prof_status"],
            prof_cfg=row["f_prof_cfg"],
            conn_id=row["f_conn_id"],
            conn_code=row["f_conn_code"],
            conn_provider=row["f_conn_provider"],
            conn_base=row["f_conn_base"],
            conn_cred=row["f_conn_cred"],
            conn_cfg=row["f_conn_cfg"],
            conn_status=row["f_conn_status"],
        )
    cond = row["condition_expression"] or {}
    if not isinstance(cond, dict):
        cond = dict(cond)
    return RouteVersion(
        id=int(row["route_id"]),
        task_code=row["task_code"],
        route_version=int(row["route_version"]),
        display_name=row["display_name"],
        primary=primary,
        fallback=fallback,
        confidence_policy=ConfidencePolicy(
            policy_code=row["conf_code"],
            minimum_system_confidence=float(row["minimum_system_confidence"] or 0.78),
            minimum_confidence=float(row["minimum_confidence"] or 0.78),
            maximum_category_score_gap_for_clarification=float(
                row["maximum_category_score_gap_for_clarification"] or 0.08
            ),
            fallback_on_invalid_schema=bool(row["fallback_on_invalid_schema"]),
            fallback_on_low_confidence=bool(row["fallback_on_low_confidence"]),
            prefer_clarification_when_ambiguous=bool(
                row["prefer_clarification_when_ambiguous"]
            ),
            clarify_on_session_conflict=bool(row["clarify_on_session_conflict"]),
            clarify_on_multiple_needs=bool(row["clarify_on_multiple_needs"]),
        ),
        timeout_policy=TimeoutPolicy(
            policy_code=row["timeout_code"],
            primary_timeout_ms=int(row["primary_timeout_ms"]),
            fallback_timeout_ms=int(row["fallback_timeout_ms"]),
            total_budget_ms=int(row["total_budget_ms"]),
            min_fallback_remaining_ms=int(row["min_fallback_remaining_ms"] or 500),
            retry_same_model=bool(row["retry_same_model"]),
        ),
        condition_expression=cond,
        traffic_weight=float(row["route_traffic_weight"]),
        priority=int(row["route_priority"]),
        effective_from=row["effective_from"],
        effective_until=row["effective_until"],
        is_active=bool(row["is_active"]),
    )
