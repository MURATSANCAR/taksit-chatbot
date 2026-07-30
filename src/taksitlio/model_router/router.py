"""ModelRouter — deployment-based FAST/FALLBACK with system confidence + deadline."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from taksitlio.model_gateway.gateway import ModelGateway
from taksitlio.model_gateway.types import (
    CompletionRequest,
    DeadlineExhaustedError,
    JsonParseError,
    ModelGatewayError,
    ProviderUnavailableError,
)
from taksitlio.model_router.confidence import SystemConfidenceEvaluator
from taksitlio.model_router.deadline import Deadline
from taksitlio.model_router.health import RuntimeHealthRegistry
from taksitlio.model_router.route_selector import (
    RouteContext,
    RouteVersion,
    RouteVersionRepository,
    select_route_version,
)
from taksitlio.model_router.router_types import (
    ReasonCode,
    RouteDecision,
    UnderstandingRequest,
    UnderstandingResult,
)


class ModelRouter:
    TASK_NEED_UNDERSTANDING = "NEED_UNDERSTANDING"

    def __init__(
        self,
        gateway: ModelGateway,
        routes: RouteVersionRepository,
        *,
        health: RuntimeHealthRegistry | None = None,
        confidence_evaluator: SystemConfidenceEvaluator | None = None,
        default_schema: Mapping[str, Any] | None = None,
    ) -> None:
        self._gateway = gateway
        self._routes = routes
        self._health = health
        self._evaluator = confidence_evaluator or SystemConfidenceEvaluator()
        self._default_schema = default_schema or _load_default_need_profile_schema()

    async def understand(self, request: UnderstandingRequest) -> UnderstandingResult:
        correlation_id = request.correlation_id or str(uuid.uuid4())
        route = self._select_route(request)
        deadline = Deadline.from_budget_ms(route.timeout_policy.total_budget_ms)
        schema = request.json_schema or self._default_schema
        messages = _build_messages(request)

        primary = route.primary
        if self._health is not None and not self._health.get(primary.id).is_callable():
            return await self._maybe_fallback(
                route,
                messages,
                deadline=deadline,
                schema=schema,
                request=request,
                correlation_id=correlation_id,
                reason=ReasonCode.PRIMARY_UNAVAILABLE,
                prior_latency_ms=0.0,
            )

        try:
            primary_timeout = deadline.clamp_timeout_ms(
                route.timeout_policy.primary_timeout_ms
            )
            if primary_timeout <= 0:
                return _safe_failure(
                    ReasonCode.DEADLINE_EXHAUSTED,
                    correlation_id=correlation_id,
                    deployment=primary.deployment_code,
                    profile=primary.profile.profile_code,
                )

            payload, result = await self._gateway.complete_json(
                primary,
                CompletionRequest(
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout_ms=primary_timeout,
                    temperature=float(primary.profile.temperature),
                    max_tokens=primary.profile.max_output_tokens,
                    correlation_id=correlation_id,
                ),
            )
        except (JsonParseError, ModelGatewayError) as exc:
            if isinstance(exc, DeadlineExhaustedError):
                return _safe_failure(
                    ReasonCode.DEADLINE_EXHAUSTED,
                    correlation_id=correlation_id,
                    deployment=primary.deployment_code,
                    profile=primary.profile.profile_code,
                )
            return await self._maybe_fallback(
                route,
                messages,
                deadline=deadline,
                schema=schema,
                request=request,
                correlation_id=correlation_id,
                reason=(
                    ReasonCode.INVALID_SCHEMA
                    if isinstance(exc, JsonParseError)
                    else ReasonCode.COMPREHENSION_FAILURE
                ),
                prior_latency_ms=0.0,
                diagnostics={"primary_error_class": getattr(exc, "error_class", "ERROR")},
            )

        valid, schema_errors = _validate_schema(payload, schema)
        evaluated = self._evaluator.evaluate(
            payload if valid else None,
            schema_valid=valid,
            schema_errors=schema_errors,
            session_summary=request.session_summary,
        )

        if not valid:
            return await self._maybe_fallback(
                route,
                messages,
                deadline=deadline,
                schema=schema,
                request=request,
                correlation_id=correlation_id,
                reason=ReasonCode.INVALID_SCHEMA,
                prior_latency_ms=result.latency_ms,
                diagnostics={"schema_errors": schema_errors},
            )

        decision = self._decide_from_system_confidence(evaluated, route)
        if decision.decision == RouteDecision.CLARIFY:
            return UnderstandingResult(
                decision=RouteDecision.CLARIFY,
                reason_code=decision.reason_code,
                need_profile=payload,
                used_deployment_code=result.deployment_code,
                used_profile_code=result.profile_code,
                latency_ms=result.latency_ms,
                system_confidence=evaluated.system_confidence,
                model_reported_confidence=evaluated.model_reported_confidence,
                clarification_question_intent=_question_intent(payload, decision.reason_code),
                missing_concepts=decision.missing_concepts,
                raw_content=result.content,
                diagnostics={
                    "signals": evaluated.signals.__dict__,
                    "details": evaluated.details,
                },
                correlation_id=correlation_id,
            )

        if decision.decision == RouteDecision.FALLBACK:
            return await self._maybe_fallback(
                route,
                messages,
                deadline=deadline,
                schema=schema,
                request=request,
                correlation_id=correlation_id,
                reason=decision.reason_code,
                prior_latency_ms=result.latency_ms,
                diagnostics={
                    "signals": evaluated.signals.__dict__,
                    "system_confidence": evaluated.system_confidence,
                    "model_reported_confidence": evaluated.model_reported_confidence,
                },
            )

        return UnderstandingResult(
            decision=RouteDecision.CONTINUE,
            reason_code=ReasonCode.OK,
            need_profile=payload,
            used_deployment_code=result.deployment_code,
            used_profile_code=result.profile_code,
            latency_ms=result.latency_ms,
            system_confidence=evaluated.system_confidence,
            model_reported_confidence=evaluated.model_reported_confidence,
            raw_content=result.content,
            diagnostics={
                "signals": evaluated.signals.__dict__,
                "details": evaluated.details,
            },
            correlation_id=correlation_id,
        )

    def _select_route(self, request: UnderstandingRequest) -> RouteVersion:
        ctx_data = dict(request.route_context or {})
        context = RouteContext(
            locale=ctx_data.get("locale"),
            client=ctx_data.get("client"),
            experiment=ctx_data.get("experiment"),
            user_segment=ctx_data.get("user_segment"),
            app_version=ctx_data.get("app_version"),
            tenant=ctx_data.get("tenant"),
            session_id=request.session_id,
            extra={
                k: v
                for k, v in ctx_data.items()
                if k
                not in {
                    "locale",
                    "client",
                    "experiment",
                    "user_segment",
                    "app_version",
                    "tenant",
                }
            },
        )
        return select_route_version(
            self._routes.list_active(self.TASK_NEED_UNDERSTANDING),
            context,
            seed=request.session_id or request.correlation_id,
        )

    def _decide_from_system_confidence(
        self,
        evaluated,
        route: RouteVersion,
    ) -> "_Decision":
        policy = route.confidence_policy
        signals = evaluated.signals

        if signals.multiple_independent_needs and policy.clarify_on_multiple_needs:
            return _Decision(
                RouteDecision.CLARIFY,
                ReasonCode.MULTIPLE_INDEPENDENT_NEEDS,
                missing_concepts=("need_priority",),
            )

        if not signals.session_consistent and policy.clarify_on_session_conflict:
            return _Decision(
                RouteDecision.CLARIFY,
                ReasonCode.SESSION_CONFLICT,
                missing_concepts=("session_resolution",),
            )

        if signals.missing_information and policy.prefer_clarification_when_ambiguous:
            missing = _missing_concepts_from_payload_signals(signals)
            reason = (
                ReasonCode.MISSING_PRODUCT_FORM
                if "device_type" in missing
                else ReasonCode.MISSING_INFORMATION
            )
            return _Decision(RouteDecision.CLARIFY, reason, missing_concepts=missing)

        if (
            signals.semantic_score_gap
            <= policy.maximum_category_score_gap_for_clarification
            and policy.prefer_clarification_when_ambiguous
            and signals.semantic_match_score > 0.0
            and signals.semantic_score_gap < 1.0
        ):
            return _Decision(
                RouteDecision.CLARIFY,
                ReasonCode.MISSING_PRODUCT_FORM,
                missing_concepts=("device_type",),
            )

        if signals.budget_ambiguity and not signals.budget_consistent:
            if policy.fallback_on_low_confidence:
                return _Decision(RouteDecision.FALLBACK, ReasonCode.BUDGET_AMBIGUITY)
            return _Decision(
                RouteDecision.CLARIFY,
                ReasonCode.BUDGET_AMBIGUITY,
                missing_concepts=("budget",),
            )

        if signals.budget_ambiguity and policy.prefer_clarification_when_ambiguous:
            return _Decision(
                RouteDecision.CLARIFY,
                ReasonCode.BUDGET_AMBIGUITY,
                missing_concepts=("budget",),
            )

        if evaluated.system_confidence < policy.min_confidence:
            if signals.comprehension_failure or policy.fallback_on_low_confidence:
                return _Decision(
                    RouteDecision.FALLBACK,
                    ReasonCode.LOW_SYSTEM_CONFIDENCE
                    if not signals.comprehension_failure
                    else ReasonCode.COMPREHENSION_FAILURE,
                )

        return _Decision(RouteDecision.CONTINUE, ReasonCode.OK)

    async def _maybe_fallback(
        self,
        route: RouteVersion,
        messages: list[dict[str, str]],
        *,
        deadline: Deadline,
        schema: Mapping[str, Any],
        request: UnderstandingRequest,
        correlation_id: str,
        reason: ReasonCode,
        prior_latency_ms: float,
        diagnostics: dict[str, Any] | None = None,
    ) -> UnderstandingResult:
        if route.fallback is None:
            return _safe_failure(
                reason if reason != ReasonCode.OK else ReasonCode.FALLBACK_FAILED,
                correlation_id=correlation_id,
                latency_ms=prior_latency_ms,
                diagnostics=diagnostics,
            )

        remaining_needed = route.timeout_policy.min_fallback_remaining_ms
        if deadline.is_exhausted(min_remaining_ms=remaining_needed):
            return _safe_failure(
                ReasonCode.DEADLINE_EXHAUSTED,
                correlation_id=correlation_id,
                latency_ms=prior_latency_ms,
                diagnostics=diagnostics,
            )

        fallback = route.fallback
        if self._health is not None and not self._health.get(fallback.id).is_callable():
            return _safe_failure(
                ReasonCode.FALLBACK_FAILED,
                correlation_id=correlation_id,
                latency_ms=prior_latency_ms,
                deployment=fallback.deployment_code,
                profile=fallback.profile.profile_code,
                diagnostics=diagnostics,
            )

        fb_timeout = deadline.clamp_timeout_ms(
            route.timeout_policy.fallback_timeout_ms,
            min_remaining_ms=remaining_needed,
        )
        if fb_timeout <= 0:
            return _safe_failure(
                ReasonCode.DEADLINE_EXHAUSTED,
                correlation_id=correlation_id,
                latency_ms=prior_latency_ms,
                diagnostics=diagnostics,
            )

        try:
            payload, result = await self._gateway.complete_json(
                fallback,
                CompletionRequest(
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout_ms=fb_timeout,
                    temperature=float(fallback.profile.temperature),
                    max_tokens=fallback.profile.max_output_tokens,
                    correlation_id=correlation_id,
                ),
            )
        except (JsonParseError, ModelGatewayError, ProviderUnavailableError):
            return _safe_failure(
                ReasonCode.FALLBACK_FAILED,
                correlation_id=correlation_id,
                latency_ms=prior_latency_ms,
                deployment=fallback.deployment_code,
                profile=fallback.profile.profile_code,
                fallback_used=True,
                diagnostics=diagnostics,
            )

        valid, schema_errors = _validate_schema(payload, schema)
        evaluated = self._evaluator.evaluate(
            payload if valid else None,
            schema_valid=valid,
            schema_errors=schema_errors,
            session_summary=request.session_summary,
        )
        if not valid:
            return _safe_failure(
                ReasonCode.FALLBACK_FAILED,
                correlation_id=correlation_id,
                latency_ms=prior_latency_ms + result.latency_ms,
                deployment=result.deployment_code,
                profile=result.profile_code,
                fallback_used=True,
                diagnostics={"schema_errors": schema_errors, **(diagnostics or {})},
            )

        decision = self._decide_from_system_confidence(evaluated, route)
        if decision.decision == RouteDecision.CLARIFY:
            return UnderstandingResult(
                decision=RouteDecision.CLARIFY,
                reason_code=decision.reason_code,
                need_profile=payload,
                used_deployment_code=result.deployment_code,
                used_profile_code=result.profile_code,
                latency_ms=prior_latency_ms + result.latency_ms,
                system_confidence=evaluated.system_confidence,
                model_reported_confidence=evaluated.model_reported_confidence,
                clarification_question_intent=_question_intent(payload, decision.reason_code),
                missing_concepts=decision.missing_concepts,
                fallback_used=True,
                raw_content=result.content,
                diagnostics={"signals": evaluated.signals.__dict__, **(diagnostics or {})},
                correlation_id=correlation_id,
            )

        if decision.decision == RouteDecision.FALLBACK:
            return _safe_failure(
                decision.reason_code,
                correlation_id=correlation_id,
                latency_ms=prior_latency_ms + result.latency_ms,
                deployment=result.deployment_code,
                profile=result.profile_code,
                fallback_used=True,
                need_profile=payload,
                system_confidence=evaluated.system_confidence,
                model_reported_confidence=evaluated.model_reported_confidence,
                diagnostics={"signals": evaluated.signals.__dict__, **(diagnostics or {})},
            )

        return UnderstandingResult(
            decision=RouteDecision.CONTINUE,
            reason_code=reason,
            need_profile=payload,
            used_deployment_code=result.deployment_code,
            used_profile_code=result.profile_code,
            latency_ms=prior_latency_ms + result.latency_ms,
            system_confidence=evaluated.system_confidence,
            model_reported_confidence=evaluated.model_reported_confidence,
            fallback_used=True,
            raw_content=result.content,
            diagnostics={"signals": evaluated.signals.__dict__, **(diagnostics or {})},
            correlation_id=correlation_id,
        )


class _Decision:
    def __init__(
        self,
        decision: RouteDecision,
        reason_code: ReasonCode,
        *,
        missing_concepts: tuple[str, ...] = (),
    ) -> None:
        self.decision = decision
        self.reason_code = reason_code
        self.missing_concepts = missing_concepts


def _missing_concepts_from_payload_signals(signals) -> tuple[str, ...]:
    if signals.missing_information:
        return ("device_type",)
    return ()


def _question_intent(payload: Mapping[str, Any], reason: ReasonCode) -> str | None:
    clarification = payload.get("clarification") or {}
    intent = clarification.get("question_intent")
    if intent:
        return str(intent)
    if reason in {ReasonCode.MISSING_PRODUCT_FORM}:
        return "device_type"
    if reason == ReasonCode.BUDGET_AMBIGUITY:
        return "budget"
    if reason == ReasonCode.MULTIPLE_INDEPENDENT_NEEDS:
        return "need_priority"
    return "usage"


def _safe_failure(
    reason: ReasonCode,
    *,
    correlation_id: str,
    latency_ms: float = 0.0,
    deployment: str | None = None,
    profile: str | None = None,
    fallback_used: bool = False,
    diagnostics: dict[str, Any] | None = None,
    need_profile: dict[str, Any] | None = None,
    system_confidence: float | None = None,
    model_reported_confidence: float | None = None,
) -> UnderstandingResult:
    return UnderstandingResult(
        decision=RouteDecision.SAFE_FAILURE,
        reason_code=reason,
        need_profile=need_profile,
        used_deployment_code=deployment,
        used_profile_code=profile,
        latency_ms=latency_ms,
        system_confidence=system_confidence,
        model_reported_confidence=model_reported_confidence,
        fallback_used=fallback_used,
        diagnostics=diagnostics or {},
        correlation_id=correlation_id,
    )


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
