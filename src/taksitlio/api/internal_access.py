"""INTERNAL cohort access control (P3.4). Guest search remains available.

Rules:
- External callers may use /v1/search-sessions without INTERNAL headers (legacy path).
- Claims of INTERNAL traffic / cohort override require a shared token.
- Cohort ID manipulation without auth is rejected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from fastapi import HTTPException, Request

from taksitlio.runtime_flags import FeatureFlagStatus, flags_from_rows


HEADER_INTERNAL_TOKEN = "X-Taksitlio-Internal-Token"
HEADER_TRAFFIC = "X-Taksitlio-Traffic"
HEADER_COHORT_ID = "X-Taksitlio-Cohort-Id"
HEADER_COHORT_VERSION = "X-Taksitlio-Cohort-Version"


@dataclass(frozen=True)
class InternalAccessDecision:
    allowed: bool
    is_internal: bool
    reason: str
    cohort_id: Optional[int] = None
    cohort_version: Optional[int] = None


def _configured_token() -> str:
    return (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip()


def evaluate_internal_access(
    headers: Mapping[str, str],
    *,
    flag_status: str,
    flag_config: Mapping[str, Any],
    configured_token: Optional[str] = None,
) -> InternalAccessDecision:
    """Pure access evaluation (unit-testable)."""

    token_cfg = (configured_token if configured_token is not None else _configured_token()).strip()
    # Normalize header keys
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    presented = (h.get(HEADER_INTERNAL_TOKEN.lower()) or "").strip()
    traffic = (h.get(HEADER_TRAFFIC.lower()) or "").strip().lower()
    cohort_raw = (h.get(HEADER_COHORT_ID.lower()) or "").strip()
    cohort_ver_raw = (h.get(HEADER_COHORT_VERSION.lower()) or "").strip()

    claims_internal = traffic == "internal" or bool(cohort_raw) or bool(presented)
    if not claims_internal:
        return InternalAccessDecision(
            allowed=True,
            is_internal=False,
            reason="external_legacy_path",
        )

    if not token_cfg:
        # Fail closed for INTERNAL claims when token not configured in runtime.
        return InternalAccessDecision(
            allowed=False,
            is_internal=True,
            reason="internal_token_not_configured",
        )
    if presented != token_cfg:
        return InternalAccessDecision(
            allowed=False,
            is_internal=True,
            reason="invalid_internal_token",
        )

    cfg_cohort = flag_config.get("cohort_id")
    cfg_ver = flag_config.get("cohort_version")
    req_cohort = int(cohort_raw) if cohort_raw.isdigit() else None
    req_ver = int(cohort_ver_raw) if cohort_ver_raw.isdigit() else None
    if req_cohort is not None and cfg_cohort is not None and int(cfg_cohort) != req_cohort:
        return InternalAccessDecision(
            allowed=False,
            is_internal=True,
            reason="cohort_id_manipulation",
        )
    if req_ver is not None and cfg_ver is not None and int(cfg_ver) != req_ver:
        return InternalAccessDecision(
            allowed=False,
            is_internal=True,
            reason="cohort_version_mismatch",
        )
    if str(flag_status) != FeatureFlagStatus.INTERNAL.value and traffic == "internal":
        # Token valid but flag not INTERNAL — allow diagnostic but mark reason.
        return InternalAccessDecision(
            allowed=True,
            is_internal=True,
            reason="internal_token_ok_flag_not_internal",
            cohort_id=int(cfg_cohort) if cfg_cohort is not None else req_cohort,
            cohort_version=int(cfg_ver) if cfg_ver is not None else req_ver,
        )
    return InternalAccessDecision(
        allowed=True,
        is_internal=True,
        reason="internal_authorized",
        cohort_id=int(cfg_cohort) if cfg_cohort is not None else req_cohort,
        cohort_version=int(cfg_ver) if cfg_ver is not None else req_ver,
    )


async def enforce_search_access(request: Request) -> InternalAccessDecision:
    """FastAPI dependency: reject forged INTERNAL claims; allow external legacy."""

    container = request.app.state.container
    flag_status = str(container.extras.get("dynamic_readiness_status") or "SHADOW")
    flag_config: dict[str, Any] = dict(container.extras.get("dynamic_readiness_config") or {})
    rows = container.extras.get("runtime_feature_flag_rows")
    if rows:
        flags = flags_from_rows(rows)
        f = flags.get("dynamic_readiness_enabled")
        if f is not None:
            flag_status = f.status.value
            flag_config = dict(f.config or {})
    elif not flag_config:
        # Best-effort load from Postgres pool when present (production).
        pool = container.extras.get("pool") or container.extras.get("pg_pool") or container.extras.get("db_pool")
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT status, config FROM runtime_feature_flags
                        WHERE flag_code='dynamic_readiness_enabled'
                        """
                    )
                if row:
                    flag_status = str(row["status"])
                    cfg = row["config"] or {}
                    if isinstance(cfg, str):
                        import json

                        cfg = json.loads(cfg)
                    flag_config = dict(cfg)
                    container.extras["dynamic_readiness_status"] = flag_status
                    container.extras["dynamic_readiness_config"] = flag_config
            except Exception:  # noqa: BLE001
                pass

    decision = evaluate_internal_access(
        {k: v for k, v in request.headers.items()},
        flag_status=flag_status,
        flag_config=flag_config,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "COHORT_ACCESS_ERROR",
                "reason": decision.reason,
            },
        )
    request.state.internal_access = decision  # type: ignore[attr-defined]
    return decision


__all__ = [
    "HEADER_COHORT_ID",
    "HEADER_COHORT_VERSION",
    "HEADER_INTERNAL_TOKEN",
    "HEADER_TRAFFIC",
    "InternalAccessDecision",
    "enforce_search_access",
    "evaluate_internal_access",
]
