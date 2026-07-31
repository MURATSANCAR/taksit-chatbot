"""OpenAI-compatible remote FAST extractor — no silent stub fallback (ADR-009)."""

from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

import httpx

from taksitlio.semantic_constraints import SemanticConstraintValidator
from taksitlio.understanding.fast.errors import (
    FastDeploymentUnavailable,
    FastExtractionError,
    NeedProfileSchemaError,
    TruncatedNeedProfileError,
)
from taksitlio.understanding.fast.protocol import FastExtractionOutcome
from taksitlio.understanding.fast.schema_utils import validate_need_profile


_FORBIDDEN_ID_PATTERNS = (
    "fixture.",
    "category-",
    "cat_",
)

_DEFAULT_SYSTEM_PROMPT = """You extract Turkish purchase needs as one compact JSON object only (NeedProfile).
Rules:
- Output minified JSON on one logical object: no markdown, no pretty-print, no extra whitespace.
- Keep need_description <= 120 chars; keep arrays short (prefer empty over filler).
- Never emit category IDs, fixture keys, or UUIDs.
- intent: {type, confidence}; type enum PRODUCT_PURCHASE|COMPARE_OPTIONS|BUDGET_INQUIRY|INSTALLMENT_INQUIRY|OUT_OF_SCOPE|CLARIFICATION_RESPONSE|OTHER
- need_description: short Turkish string from the utterance
- budget: {type, value, minimum, maximum, monthly_payment, currency}; type UNKNOWN/EXACT/APPROXIMATE/RANGE/MONTHLY_PAYMENT; currency TRY; unused numerics null
- preferences: [{concept, importance}] — positive wants only
- usage_context: string array (usually empty)
- entities: [{type, value, confidence?}]
- ambiguities: [{code, description}] (usually empty)
- clarification: {required, question_intent}
- confidence: 0..1
- semantic_constraints: {positive, negative, corrections} each [{concept, provenance, weight?}]; provenance EXPLICIT|INFERRED|EXPLICIT_NEGATION|USER_CORRECTION|SESSION_CONTEXT
CRITICAL for Turkish utterances:
- Every wanted product/concept MUST appear in semantic_constraints.positive with provenance EXPLICIT.
- Every rejected/excluded product (istemiyorum, değil, boşver, yerine) MUST appear in semantic_constraints.negative with provenance EXPLICIT_NEGATION.
- If the user corrects (yanlış, demedim, özür, değil X lazım Y): add corrections with previous_concept+replacement_concept when possible; also put Y in positive and X in negative.
- Put exclusions ONLY in semantic_constraints.negative — never as low-importance preferences.
No markdown."""


@lru_cache(maxsize=1)
def _need_profile_schema() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[2] / "schemas" / "need_profile.schema.json"
    )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _looks_like_forbidden_identifier(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    if any(p in lowered for p in _FORBIDDEN_ID_PATTERNS):
        return True
    # UUID-ish
    if len(lowered) >= 32 and lowered.count("-") >= 4:
        parts = lowered.replace("-", "")
        if all(c in "0123456789abcdef" for c in parts):
            return True
    return False


class RemoteFastExtractor:
    """Calls an OpenAI-compatible chat endpoint resolved from deployment config.

    Model name / base URL are injected — never hardcoded. On any transport or
    schema failure the caller receives a typed error; DeterministicFastExtractor
    is never substituted here.
    """

    name = "remote_openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model_reference: str,
        timeout_ms: int = 3000,
        temperature: float = 0.0,
        max_output_tokens: int = 512,
        chat_path: str = "/v1/chat/completions",
        api_key: Optional[str] = None,
        deployment_code: str = "runtime-fast",
        profile_code: str = "FAST_UNDERSTANDING",
        client: Optional[httpx.AsyncClient] = None,
        validator: Optional[SemanticConstraintValidator] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise FastDeploymentUnavailable("FAST base_url empty")
        if not model_reference or not model_reference.strip():
            raise FastDeploymentUnavailable("FAST model_reference empty")
        self._base_url = base_url.rstrip("/")
        self._model_reference = model_reference
        self._timeout_ms = timeout_ms
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._chat_path = chat_path if chat_path.startswith("/") else f"/{chat_path}"
        self._api_key = api_key
        self._deployment_code = deployment_code
        self._profile_code = profile_code
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._validator = validator or SemanticConstraintValidator()
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def extract(
        self,
        utterance: str,
        *,
        locale: str = "tr-TR",
        session_summary: Optional[Mapping[str, Any]] = None,
    ) -> FastExtractionOutcome:
        correlation_id = str(uuid4())
        url = f"{self._base_url}{self._chat_path}"
        headers = {"X-Correlation-ID": correlation_id}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        user_payload: dict[str, Any] = {
            "utterance": utterance,
            "locale": locale,
        }
        if session_summary:
            # Conversation context only — never annotated semantic constraints.
            user_payload["conversation_context"] = dict(session_summary)

        body = {
            "model": self._model_reference,
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "stream": False,
            # Prefer constrained JSON when the OpenAI-compatible server supports it;
            # llama.cpp accepts json_schema; unknown keys are ignored by some servers.
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "NeedProfile",
                    "schema": _need_profile_schema(),
                    "strict": True,
                },
            },
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }

        started = time.perf_counter()
        try:
            response = await self._client.post(
                url,
                json=body,
                headers=headers,
                timeout=max(self._timeout_ms / 1000.0, 0.5),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise FastDeploymentUnavailable(
                f"FAST timeout after {self._timeout_ms}ms"
            ) from exc
        except httpx.HTTPError as exc:
            raise FastDeploymentUnavailable(f"FAST HTTP error: {exc}") from exc

        latency_ms = (time.perf_counter() - started) * 1000.0
        content = self._extract_content(payload)
        # Validate returned model name when present.
        returned_model = payload.get("model") if isinstance(payload, dict) else None
        if isinstance(returned_model, str) and returned_model and returned_model != self._model_reference:
            raise FastExtractionError(
                f"model name mismatch: expected {self._model_reference!r} "
                f"got {returned_model!r}",
                reason_code="MODEL_NAME_MISMATCH",
            )

        finish_reason = None
        usage = payload.get("usage") if isinstance(payload, dict) else None
        try:
            finish_reason = (payload.get("choices") or [{}])[0].get("finish_reason")
        except Exception:  # noqa: BLE001
            finish_reason = None
        completion_tokens = None
        if isinstance(usage, Mapping) and isinstance(usage.get("completion_tokens"), (int, float)):
            completion_tokens = int(usage["completion_tokens"])

        try:
            need_profile = json.loads(content)
        except json.JSONDecodeError as exc:
            if finish_reason == "length" or (
                completion_tokens is not None
                and completion_tokens >= self._max_output_tokens
            ):
                raise TruncatedNeedProfileError(
                    f"truncated JSON from FAST at max_tokens={self._max_output_tokens}: {exc}",
                    issues=[str(exc)],
                ) from exc
            raise NeedProfileSchemaError(
                f"invalid JSON from FAST: {exc}", issues=[str(exc)]
            ) from exc

        try:
            validate_need_profile(need_profile)
        except NeedProfileSchemaError:
            if finish_reason == "length":
                raise TruncatedNeedProfileError(
                    f"NeedProfile incomplete at max_tokens={self._max_output_tokens}",
                    issues=["finish_reason=length"],
                )
            raise

        # Forbidden identifier scan across string leaves.
        forbidden_hits = list(self._scan_forbidden(need_profile))
        if forbidden_hits:
            raise FastExtractionError(
                "FAST emitted forbidden catalog identifiers",
                reason_code="FORBIDDEN_IDENTIFIER_GENERATION",
            )

        # Validator expects the constraint bag, not the full NeedProfile.
        raw_constraints = need_profile.get("semantic_constraints")
        if not isinstance(raw_constraints, Mapping):
            raw_constraints = {}
        constraints = self._validator.validate(raw_constraints)
        return FastExtractionOutcome(
            utterance=utterance,
            need_profile=need_profile,
            constraints=constraints,
            extractor=self.name,
            latency_ms=latency_ms,
            diagnostics={
                "deployment_code": self._deployment_code,
                "profile_code": self._profile_code,
                "correlation_id": correlation_id,
                "forbidden_identifier_hits": forbidden_hits,
                "usage": usage if isinstance(usage, Mapping) else None,
                "finish_reason": finish_reason,
            },
        )

    @staticmethod
    def _extract_content(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise NeedProfileSchemaError("FAST response is not a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise NeedProfileSchemaError("FAST response missing choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise NeedProfileSchemaError("FAST response missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise NeedProfileSchemaError("FAST response content empty")
        return content.strip()

    @classmethod
    def _scan_forbidden(cls, node: Any) -> list[str]:
        hits: list[str] = []
        if isinstance(node, str):
            if _looks_like_forbidden_identifier(node):
                hits.append(node[:80])
        elif isinstance(node, Mapping):
            for v in node.values():
                hits.extend(cls._scan_forbidden(v))
        elif isinstance(node, list):
            for v in node:
                hits.extend(cls._scan_forbidden(v))
        return hits


def build_remote_fast_from_env() -> RemoteFastExtractor:
    """Construct from environment — raises FastDeploymentUnavailable if unset."""

    import os

    base = (os.environ.get("FAST_PROVIDER_BASE_URL") or os.environ.get("POC_FAST_BASE_URL") or "").strip()
    model = (
        os.environ.get("FAST_MODEL_REFERENCE")
        or os.environ.get("POC_FAST_MODEL_REFERENCE")
        or ""
    ).strip()
    if not base or not model:
        raise FastDeploymentUnavailable(
            "FAST_PROVIDER_BASE_URL / FAST_MODEL_REFERENCE required"
        )
    # CPU-hosted 4B GGUF NeedProfile fills commonly exceed 3s/128 tokens;
    # override via env — defaults remain aggressive for GPU-class targets.
    timeout_ms = int(os.environ.get("FAST_TIMEOUT_MS") or os.environ.get("POC_FAST_TIMEOUT_MS") or "3000")
    max_tokens = int(
        os.environ.get("FAST_MAX_OUTPUT_TOKENS")
        or os.environ.get("POC_FAST_MAX_OUTPUT_TOKENS")
        or "512"
    )
    temperature = float(
        os.environ.get("FAST_TEMPERATURE") or os.environ.get("POC_FAST_TEMPERATURE") or "0"
    )
    return RemoteFastExtractor(
        base_url=base,
        model_reference=model,
        timeout_ms=timeout_ms,
        max_output_tokens=max_tokens,
        temperature=temperature,
        api_key=(os.environ.get("FAST_API_KEY") or None),
        deployment_code=os.environ.get("FAST_DEPLOYMENT_CODE") or "runtime-fast",
        profile_code=os.environ.get("FAST_PROFILE_CODE") or "FAST_UNDERSTANDING",
    )


__all__ = ["RemoteFastExtractor", "build_remote_fast_from_env"]
