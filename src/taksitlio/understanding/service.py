"""Understanding service — FAST/FALLBACK + conversation UPDATE."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from taksitlio.conversation.session import ConversationStateManager, SessionState
from taksitlio.conversation.state import apply_conversation_update
from taksitlio.model_gateway.gateway import CompletionRequest, ModelGateway, ModelGatewayError
from taksitlio.model_router.router import (
    ModelRouter,
    RouteDecision,
    UnderstandingRequest,
    UnderstandingResult,
)


class PromptProvider(Protocol):
    async def get_active(self, prompt_code: str) -> str: ...


class StaticPromptProvider:
    def __init__(self, prompts: Mapping[str, str]) -> None:
        self._prompts = dict(prompts)

    async def get_active(self, prompt_code: str) -> str:
        try:
            return self._prompts[prompt_code]
        except KeyError as exc:
            raise KeyError(f"No active prompt for code: {prompt_code}") from exc


DEFAULT_NEED_PROMPT = """Sen Taksitlio Türkçe ihtiyaç anlama motorusun.
Kullanıcı mesajından yapılandırılmış ihtiyaç profili çıkar.
Kategori kodu ÜRETME. Kampanya seçme. Finansal tavsiye verme.
Sadece geçerli JSON döndür. Thinking kullanma.
"""

DEFAULT_UPDATE_PROMPT = """Sen Taksitlio konuşma güncelleme motorusun.
Mevcut session ihtiyacına uygulanacak ConversationUpdate JSON üret.
Sadece geçerli JSON döndür.
"""


@dataclass(frozen=True)
class UnderstoodTurn:
    session: SessionState
    understanding: UnderstandingResult
    need_profile: dict[str, Any] | None
    was_update: bool


class UnderstandingService:
    def __init__(
        self,
        router: ModelRouter,
        gateway: ModelGateway,
        sessions: ConversationStateManager,
        prompts: PromptProvider,
        *,
        update_schema: Mapping[str, Any] | None = None,
    ) -> None:
        self._router = router
        self._gateway = gateway
        self._sessions = sessions
        self._prompts = prompts
        self._update_schema = update_schema or _load_update_schema()

    @property
    def sessions(self) -> ConversationStateManager:
        return self._sessions

    async def process_message(
        self,
        *,
        session_id: str,
        message: str,
        user_id: str | None = None,
    ) -> UnderstoodTurn:
        session = await self._sessions.get_or_create(session_id, user_id=user_id)

        if session.need_profile and session.turn_count > 0:
            updated = await self._try_conversation_update(session, message)
            if updated is not None:
                return updated

        system_prompt = await self._safe_prompt("NEED_UNDERSTANDING", DEFAULT_NEED_PROMPT)
        result = await self._router.understand(
            UnderstandingRequest(
                message=message,
                session_summary=session.summary() if session.need_profile else None,
                system_prompt=system_prompt,
            )
        )

        need_profile = result.need_profile
        if result.decision == RouteDecision.CONTINUE and need_profile:
            session = await self._sessions.apply_need_profile(
                session_id,
                need_profile,
                clarification_intent=None,
            )
        elif result.decision == RouteDecision.CLARIFY and need_profile:
            session = await self._sessions.apply_need_profile(
                session_id,
                need_profile,
                clarification_intent=result.clarification_question_intent,
            )

        return UnderstoodTurn(
            session=session,
            understanding=result,
            need_profile=need_profile,
            was_update=False,
        )

    async def _try_conversation_update(
        self,
        session: SessionState,
        message: str,
    ) -> UnderstoodTurn | None:
        route = self._router._routes.get_route(ModelRouter.TASK_NEED_UNDERSTANDING)
        system_prompt = await self._safe_prompt(
            "CONVERSATION_UPDATE", DEFAULT_UPDATE_PROMPT
        )
        user_content = (
            f"Mesaj: {message}\n\n"
            f"Mevcut ihtiyaç:\n{json.dumps(session.need_profile, ensure_ascii=False)}"
        )
        try:
            payload, completion = await self._gateway.complete_json(
                route.primary,
                CompletionRequest(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    timeout_ms=route.timeout_policy.primary_timeout_ms,
                    temperature=float(route.primary.temperature),
                    max_tokens=route.primary.max_output_tokens,
                ),
            )
        except (ModelGatewayError, KeyError):
            return None

        valid, _ = _validate(payload, self._update_schema)
        if not valid:
            return None

        operation = payload.get("operation")
        if operation == "CLARIFY":
            # Fall through to full understanding via caller returning None? 
            # Prefer treating as update path with clarification signal.
            understanding = UnderstandingResult(
                decision=RouteDecision.CLARIFY,
                need_profile=session.need_profile,
                used_profile_code=completion.profile_code,
                latency_ms=completion.latency_ms,
                clarification_question_intent=(
                    (payload.get("need_profile") or {})
                    .get("clarification", {})
                    .get("question_intent")
                ),
                reason="conversation_clarify",
            )
            return UnderstoodTurn(
                session=session,
                understanding=understanding,
                need_profile=session.need_profile,
                was_update=True,
            )

        if operation == "REPLACE" and not payload.get("need_profile"):
            return None

        try:
            new_profile = apply_conversation_update(session.need_profile, payload)
        except ValueError:
            return None

        session = await self._sessions.apply_need_profile(
            session.session_id,
            new_profile,
        )
        understanding = UnderstandingResult(
            decision=RouteDecision.CONTINUE,
            need_profile=new_profile,
            used_profile_code=completion.profile_code,
            latency_ms=completion.latency_ms,
            reason="conversation_update",
        )
        return UnderstoodTurn(
            session=session,
            understanding=understanding,
            need_profile=new_profile,
            was_update=True,
        )

    async def _safe_prompt(self, code: str, default: str) -> str:
        try:
            return await self._prompts.get_active(code)
        except KeyError:
            return default


def _validate(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    # conversation_update schema $ref to need_profile may fail without resolver;
    # validate core fields leniently when full Draft validation fails on $ref.
    try:
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        # Ignore $ref resolution errors for need_profile optional field
        hard = [
            e
            for e in errors
            if "need_profile" not in [str(p) for p in e.path]
            and "$ref" not in e.message
        ]
        if not hard:
            required = {"operation", "updates", "preserve", "confidence"}
            if required.issubset(payload.keys()):
                return True, []
        return False, [e.message for e in hard]
    except Exception:
        required = {"operation", "updates", "preserve", "confidence"}
        return required.issubset(payload.keys()), []


def _load_update_schema() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "conversation_update.schema.json"
    )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
