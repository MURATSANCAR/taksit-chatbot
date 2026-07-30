"""ModelRouter — FAST → validate → confidence policy → clarify / FALLBACK."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from taksitlio.model_gateway.gateway import (
    CompletionRequest,
    ModelGateway,
    ModelGatewayError,
    ModelProfile,
)


class RouteDecision(str, Enum):
    CONTINUE = "CONTINUE"
    CLARIFY = "CLARIFY"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class ConfidencePolicy:
    policy_code: str
    minimum_confidence: float = 0.78
    maximum_category_score_gap_for_clarification: float = 0.08
    fallback_on_invalid_schema: bool = True
    fallback_on_conflict: bool = True
    fallback_on_multiple_needs: bool = True
    fallback_on_budget_confusion: bool = True
    fallback_on_low_confidence: bool = True
    prefer_clarification_when_ambiguous: bool = True


@dataclass(frozen=True)
class TimeoutPolicy:
    policy_code: str
    primary_timeout_ms: int = 3000
    fallback_timeout_ms: int = 8000
    total_budget_ms: int = 10000
    retry_same_model: bool = False


@dataclass(frozen=True)
class TaskRoute:
    task_code: str
    primary: ModelProfile
    fallback: ModelProfile | None
    confidence_policy: ConfidencePolicy
    timeout_policy: TimeoutPolicy


@dataclass(frozen=True)
class UnderstandingRequest:
    message: str
    session_summary: Mapping[str, Any] | None = None
    system_prompt: str = ""
    json_schema: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class UnderstandingResult:
    decision: RouteDecision
    need_profile: dict[str, Any] | None
    used_profile_code: str
    latency_ms: float
    clarification_question_intent: str | None = None
    reason: str | None = None
    fallback_used: bool = False
    raw_content: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


class TaskRouteRepository(Protocol):
    def get_route(self, task_code: str) -> TaskRoute: ...


class ModelRouter:
    """
    Routes NEED_UNDERSTANDING through FAST primary, then policy-driven fallback.

    Never embeds model names; resolves everything from TaskRoute / DB profiles.
    """

    TASK_NEED_UNDERSTANDING = "NEED_UNDERSTANDING"

    def __init__(
        self,
        gateway: ModelGateway,
        routes: TaskRouteRepository,
        *,
        default_schema: Mapping[str, Any] | None = None,
    ) -> None:
        self._gateway = gateway
        self._routes = routes
        self._default_schema = default_schema or _load_default_need_profile_schema()

    async def understand(self, request: UnderstandingRequest) -> UnderstandingResult:
        route = self._routes.get_route(self.TASK_NEED_UNDERSTANDING)
        schema = request.json_schema or self._default_schema
        messages = _build_messages(request)

        try:
            primary_payload, primary_result = await self._gateway.complete_json(
                route.primary,
                CompletionRequest(
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout_ms=route.timeout_policy.primary_timeout_ms,
                    temperature=float(route.primary.temperature),
                    max_tokens=route.primary.max_output_tokens,
                ),
            )
        except ModelGatewayError as exc:
            if route.confidence_policy.fallback_on_invalid_schema and route.fallback:
                return await self._run_fallback(
                    route,
                    messages,
                    reason=f"primary_error:{exc}",
                )
            return UnderstandingResult(
                decision=RouteDecision.FALLBACK,
                need_profile=None,
                used_profile_code=route.primary.profile_code,
                latency_ms=0.0,
                reason=str(exc),
                fallback_used=False,
            )

        valid, schema_errors = _validate_schema(primary_payload, schema)
        if not valid:
            if route.confidence_policy.fallback_on_invalid_schema and route.fallback:
                return await self._run_fallback(
                    route,
                    messages,
                    reason="invalid_schema",
                    diagnostics={"schema_errors": schema_errors},
                )
            return UnderstandingResult(
                decision=RouteDecision.FALLBACK,
                need_profile=None,
                used_profile_code=primary_result.profile_code,
                latency_ms=primary_result.latency_ms,
                reason="invalid_schema",
                raw_content=primary_result.content,
                diagnostics={"schema_errors": schema_errors},
            )

        decision, reason = self._apply_confidence_policy(
            primary_payload, route.confidence_policy
        )

        if decision == RouteDecision.CLARIFY:
            clarification = primary_payload.get("clarification") or {}
            return UnderstandingResult(
                decision=RouteDecision.CLARIFY,
                need_profile=primary_payload,
                used_profile_code=primary_result.profile_code,
                latency_ms=primary_result.latency_ms,
                clarification_question_intent=clarification.get("question_intent"),
                reason=reason,
                raw_content=primary_result.content,
            )

        if decision == RouteDecision.FALLBACK and route.fallback:
            return await self._run_fallback(
                route,
                messages,
                reason=reason or "policy_fallback",
                prior_latency_ms=primary_result.latency_ms,
            )

        return UnderstandingResult(
            decision=RouteDecision.CONTINUE,
            need_profile=primary_payload,
            used_profile_code=primary_result.profile_code,
            latency_ms=primary_result.latency_ms,
            reason=reason,
            raw_content=primary_result.content,
        )

    async def _run_fallback(
        self,
        route: TaskRoute,
        messages: list[dict[str, str]],
        *,
        reason: str,
        diagnostics: dict[str, Any] | None = None,
        prior_latency_ms: float = 0.0,
    ) -> UnderstandingResult:
        assert route.fallback is not None
        try:
            payload, result = await self._gateway.complete_json(
                route.fallback,
                CompletionRequest(
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout_ms=route.timeout_policy.fallback_timeout_ms,
                    temperature=float(route.fallback.temperature),
                    max_tokens=route.fallback.max_output_tokens,
                ),
            )
        except ModelGatewayError as exc:
            return UnderstandingResult(
                decision=RouteDecision.FALLBACK,
                need_profile=None,
                used_profile_code=route.fallback.profile_code,
                latency_ms=prior_latency_ms,
                reason=f"fallback_error:{exc}",
                fallback_used=True,
                diagnostics=diagnostics or {},
            )

        valid, schema_errors = _validate_schema(payload, self._default_schema)
        if not valid:
            return UnderstandingResult(
                decision=RouteDecision.FALLBACK,
                need_profile=None,
                used_profile_code=result.profile_code,
                latency_ms=prior_latency_ms + result.latency_ms,
                reason="fallback_invalid_schema",
                fallback_used=True,
                raw_content=result.content,
                diagnostics={"schema_errors": schema_errors, **(diagnostics or {})},
            )

        clarification = payload.get("clarification") or {}
        if clarification.get("required") and route.confidence_policy.prefer_clarification_when_ambiguous:
            return UnderstandingResult(
                decision=RouteDecision.CLARIFY,
                need_profile=payload,
                used_profile_code=result.profile_code,
                latency_ms=prior_latency_ms + result.latency_ms,
                clarification_question_intent=clarification.get("question_intent"),
                reason=reason,
                fallback_used=True,
                raw_content=result.content,
            )

        return UnderstandingResult(
            decision=RouteDecision.CONTINUE,
            need_profile=payload,
            used_profile_code=result.profile_code,
            latency_ms=prior_latency_ms + result.latency_ms,
            reason=reason,
            fallback_used=True,
            raw_content=result.content,
            diagnostics=diagnostics or {},
        )

    @staticmethod
    def _apply_confidence_policy(
        payload: Mapping[str, Any],
        policy: ConfidencePolicy,
    ) -> tuple[RouteDecision, str | None]:
        signals = payload.get("signals") or {}
        clarification = payload.get("clarification") or {}
        confidence = float(payload.get("confidence") or 0.0)
        ambiguities = payload.get("ambiguities") or []

        if signals.get("multiple_needs") and policy.fallback_on_multiple_needs:
            return RouteDecision.FALLBACK, "multiple_needs"

        if signals.get("budget_payment_confusion") and policy.fallback_on_budget_confusion:
            return RouteDecision.FALLBACK, "budget_payment_confusion"

        if signals.get("conflicts_with_session") and policy.fallback_on_conflict:
            return RouteDecision.FALLBACK, "session_conflict"

        if signals.get("indirect_or_complex") and confidence < policy.minimum_confidence:
            return RouteDecision.FALLBACK, "indirect_or_complex"

        if (
            clarification.get("required")
            and policy.prefer_clarification_when_ambiguous
            and confidence >= policy.minimum_confidence
        ):
            return RouteDecision.CLARIFY, "clarification_required"

        if ambiguities and policy.prefer_clarification_when_ambiguous:
            category_gap = signals.get("category_score_gap")
            if (
                isinstance(category_gap, (int, float))
                and category_gap
                <= policy.maximum_category_score_gap_for_clarification
            ):
                return RouteDecision.CLARIFY, "close_category_candidates"

        if confidence < policy.minimum_confidence and policy.fallback_on_low_confidence:
            return RouteDecision.FALLBACK, "low_confidence"

        return RouteDecision.CONTINUE, None


def _build_messages(request: UnderstandingRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    user_parts = [request.message]
    if request.session_summary:
        user_parts.append(
            "Session özeti (tam geçmiş değil):\n"
            + json.dumps(request.session_summary, ensure_ascii=False)
        )
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
    return messages


def _validate_schema(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if not errors:
        return True, []
    return False, [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors]


def _load_default_need_profile_schema() -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parents[1] / "schemas" / "need_profile.schema.json"
    )
    with schema_path.open(encoding="utf-8") as fh:
        return json.load(fh)
