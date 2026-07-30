"""Route version selection — conditions + deterministic weighted traffic."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from taksitlio.model_gateway.types import ModelDeployment
from taksitlio.model_router.router_types import ConfidencePolicy, TimeoutPolicy


@dataclass(frozen=True)
class RouteVersion:
    id: int
    task_code: str
    route_version: int
    display_name: str
    primary: ModelDeployment
    fallback: ModelDeployment | None
    confidence_policy: ConfidencePolicy
    timeout_policy: TimeoutPolicy
    condition_expression: Mapping[str, Any] = field(default_factory=dict)
    traffic_weight: float = 1.0
    priority: int = 100
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    is_active: bool = True


@dataclass(frozen=True)
class RouteContext:
    locale: str | None = None
    client: str | None = None
    experiment: str | None = None
    user_segment: str | None = None
    app_version: str | None = None
    tenant: str | None = None
    session_id: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = {
            "locale": self.locale,
            "client": self.client,
            "experiment": self.experiment,
            "user_segment": self.user_segment,
            "app_version": self.app_version,
            "tenant": self.tenant,
        }
        data.update(dict(self.extra))
        return {k: v for k, v in data.items() if v is not None}


class RouteVersionRepository(Protocol):
    def list_active(self, task_code: str) -> Sequence[RouteVersion]: ...


def matches_condition(condition: Mapping[str, Any], context: RouteContext) -> bool:
    if not condition:
        return True
    ctx = context.as_dict()
    for key, expected in condition.items():
        if key == "traffic_percentage":
            continue
        if key not in ctx:
            return False
        if ctx[key] != expected:
            return False
    return True


def select_route_version(
    candidates: Sequence[RouteVersion],
    context: RouteContext,
    *,
    now: datetime | None = None,
    seed: str | None = None,
) -> RouteVersion:
    clock = now or datetime.now(timezone.utc)
    matched: list[RouteVersion] = []
    for route in candidates:
        if not route.is_active:
            continue
        if route.effective_from and clock < _aware(route.effective_from):
            continue
        if route.effective_until and clock > _aware(route.effective_until):
            continue
        if not matches_condition(route.condition_expression, context):
            continue
        matched.append(route)

    if not matched:
        raise KeyError("No active route version matches context")

    # Highest priority first; within same priority, weighted pick
    matched.sort(key=lambda r: (-r.priority, r.route_version))
    top_priority = matched[0].priority
    band = [r for r in matched if r.priority == top_priority]
    if len(band) == 1:
        return band[0]
    return _weighted_pick(band, seed or context.session_id or "default")


def _weighted_pick(routes: Sequence[RouteVersion], seed: str) -> RouteVersion:
    total = sum(max(0.0, float(r.traffic_weight)) for r in routes)
    if total <= 0:
        return routes[0]
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    # deterministic float in [0, total)
    bucket = (int(digest[:8], 16) / 0xFFFFFFFF) * total
    cursor = 0.0
    for route in routes:
        cursor += max(0.0, float(route.traffic_weight))
        if bucket <= cursor:
            return route
    return routes[-1]


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
