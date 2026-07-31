"""Understanding service — FAST/FALLBACK + optional typed conversation patch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from taksitlio.conversation.patch import ConversationPatchError, apply_conversation_patch
from taksitlio.conversation.session import ConversationStateManager, SessionState
from taksitlio.model_gateway.gateway import ModelGateway
from taksitlio.model_router.router import ModelRouter
from taksitlio.model_router.router_types import (
    ReasonCode,
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
Sistemde / girdide olmayan ürün, fiyat, taksit, banka veya kampanya bilgisi UYDURMA.
Genel sohbet, hava durumu, ödev, çeviri, siyaset veya açık dünya sorularına cevap VERME;
bunlarda intent.type=OUT_OF_SCOPE kullan.
Sadece geçerli JSON döndür. Thinking kullanma.
"""

DEFAULT_UPDATE_PROMPT = """Sen Taksitlio konuşma güncelleme motorusun.
Typed ConversationPatch JSON üret (SET/REMOVE/APPEND/REPLACE_COLLECTION/RESET_NEED).
old_value gönderme. path allowlist JSON Pointer olsun.
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
    ) -> None:
        self._router = router
        self._gateway = gateway
        self._sessions = sessions
        self._prompts = prompts

    @property
    def sessions(self) -> ConversationStateManager:
        return self._sessions

    async def process_message(
        self,
        *,
        session_id: str,
        message: str,
        user_id: str | None = None,
        route_context: dict[str, Any] | None = None,
    ) -> UnderstoodTurn:
        session = await self._sessions.get_or_create(session_id, user_id=user_id)
        system_prompt = await self._safe_prompt("NEED_UNDERSTANDING", DEFAULT_NEED_PROMPT)
        result = await self._router.understand(
            UnderstandingRequest(
                message=message,
                session_summary=session.summary() if session.need_profile else None,
                system_prompt=system_prompt,
                session_id=session_id,
                route_context=route_context
                or {"locale": "tr-TR", "client": "MOBILE"},
            )
        )

        need_profile = result.need_profile
        if result.decision in {RouteDecision.CONTINUE, RouteDecision.CLARIFY} and need_profile:
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

    async def apply_typed_patch(
        self,
        session_id: str,
        patch: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> SessionState:
        session = await self._sessions.get_or_create(session_id)
        try:
            new_profile = apply_conversation_patch(
                session.need_profile,
                patch,
                expected_version=expected_version,
                current_version=session.turn_count,
            )
        except ConversationPatchError:
            raise
        return await self._sessions.apply_need_profile(session_id, new_profile)

    async def _safe_prompt(self, code: str, default: str) -> str:
        try:
            return await self._prompts.get_active(code)
        except KeyError:
            return default
