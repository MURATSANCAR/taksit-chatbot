"""
Universal guest turn handler — routes ALL utterances:

  SMALLTALK | FAQ | NEEDS | COMPLEX_NEED | REFINEMENT | OOS | UNKNOWN

Wraps existing GuestEntryHandler / NeedsAnalysisService without breaking
the production needs→campaign path.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from taksitlio.guest.complex_constraints import (
    build_clarify_message,
    extract_complex_constraints,
)
from taksitlio.guest.faq import (
    answer_faq,
    answer_oos,
    answer_smalltalk,
    answer_unknown,
)
from taksitlio.guest.intent_router import GuestIntent, route_intent

logger = logging.getLogger(__name__)


class UniversalGuestHandler:
    """
    Single entry for guest turns. Prefer this from Chat API GUEST branch.

    Dependencies:
      - entry_handler: GuestEntryHandler (existing)
    """

    def __init__(self, entry_handler: Any) -> None:
        self._entry = entry_handler

    async def start_session(self, **kwargs) -> dict[str, Any]:
        result = await self._entry.start_session(**kwargs)
        return result.to_api_payload() if hasattr(result, "to_api_payload") else result

    async def handle_turn(
        self,
        *,
        session_id: str,
        utterance: str,
        expected_revision: int = 0,
        client_message_id: Optional[str] = None,
        client_sequence: int = 1,
        locale: str = "tr-TR",
        phase_hint: Optional[str] = None,
    ) -> dict[str, Any]:
        # Discover current phase if not provided
        phase = phase_hint
        if phase is None:
            phase = await self._safe_phase(session_id)

        decision = route_intent(utterance, phase=phase)
        logger.info(
            "guest_route session=%s intent=%s conf=%.2f reason=%s",
            session_id,
            decision.intent.value,
            decision.confidence,
            decision.reason,
        )

        # ---- FAQ / smalltalk / OOS / unknown: no state machine needed ----
        if decision.intent == GuestIntent.SMALLTALK:
            # First smalltalk on fresh session → opening via entry
            if phase in (None, "OPENING", "AWAITING_NEED") and not phase_hint:
                try:
                    result = await self._entry.start_session(
                        client_message_id=client_message_id, locale=locale
                    )
                    # If session already exists, fall through to soft smalltalk
                    if hasattr(result, "to_api_payload"):
                        return result.to_api_payload()
                    return result
                except Exception:
                    pass
            ans = answer_smalltalk(utterance)
            return self._static_payload(session_id, expected_revision, ans, decision)

        if decision.intent == GuestIntent.FAQ:
            ans = answer_faq(decision.faq_key or "")
            return self._static_payload(session_id, expected_revision, ans, decision)

        if decision.intent == GuestIntent.OOS:
            ans = answer_oos()
            return self._static_payload(session_id, expected_revision, ans, decision)

        if decision.intent == GuestIntent.UNKNOWN:
            ans = answer_unknown()
            return self._static_payload(session_id, expected_revision, ans, decision)

        # ---- COMPLEX_NEED: extract constraints, maybe clarify, else needs path ----
        if decision.intent == GuestIntent.COMPLEX_NEED:
            constraints = extract_complex_constraints(utterance)
            if constraints.clarify_missing:
                msg = build_clarify_message(constraints)
                return {
                    "session_id": session_id,
                    "phase": "CLARIFY",
                    "revision": expected_revision,
                    "messages": [{"role": "assistant", "content": msg, "type": "text"}],
                    "diagnostics": {
                        "intent": decision.intent.value,
                        "constraints": {
                            "category_hints": constraints.category_hints,
                            "budget_value": constraints.budget_value,
                            "clarify_missing": constraints.clarify_missing,
                            "preferences": constraints.to_preferences(),
                        },
                    },
                }
            # Enough signal → delegate to existing needs pipeline
            # (utterance still carries product+budget for FAST)
            result = await self._entry.handle_turn(
                session_id=session_id,
                user_utterance=utterance,
                expected_revision=expected_revision,
                client_message_id=client_message_id or "complex-1",
                client_sequence=client_sequence,
                locale=locale,
            )
            payload = result.to_api_payload() if hasattr(result, "to_api_payload") else result
            payload.setdefault("diagnostics", {})
            payload["diagnostics"]["intent"] = decision.intent.value
            payload["diagnostics"]["constraints"] = constraints.to_preferences()
            return payload

        # ---- REFINEMENT / NEEDS_ANALYSIS → existing entry handler ----
        result = await self._entry.handle_turn(
            session_id=session_id,
            user_utterance=utterance,
            expected_revision=expected_revision,
            client_message_id=client_message_id or "turn-1",
            client_sequence=client_sequence,
            locale=locale,
        )
        payload = result.to_api_payload() if hasattr(result, "to_api_payload") else result
        payload.setdefault("diagnostics", {})
        payload["diagnostics"]["intent"] = decision.intent.value
        return payload

    async def _safe_phase(self, session_id: str) -> Optional[str]:
        try:
            from uuid import UUID

            state = await self._entry._state.get_session(UUID(str(session_id)))
            guest = (state.resolved_context or {}).get("guest") or {}
            return guest.get("phase")
        except Exception:
            return None

    def _static_payload(
        self,
        session_id: str,
        revision: int,
        ans: dict[str, Any],
        decision: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "phase": "FAQ" if decision.intent.value == "FAQ" else decision.intent.value,
            "revision": revision,
            "messages": [
                {"role": "assistant", "content": ans["reply"], "type": "text"}
            ],
            "diagnostics": {
                "intent": decision.intent.value,
                "faq_key": getattr(decision, "faq_key", None),
                "reason": decision.reason,
            },
        }
        if ans.get("membership_cta"):
            payload["membership_cta"] = ans["membership_cta"]
        return payload
