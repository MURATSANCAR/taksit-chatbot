"""Optional small/fast-LLM fallback for guest category resolution (Tier 2).

Design contract
---------------
* The deterministic 28-category lexicon (need_extraction) runs FIRST and is
  sub-ms. This fallback is consulted ONLY when the lexicon fails to resolve a
  category — i.e. the hard tail (novel phrasing, typos, unusual products).
* It is env-gated: absent config -> ``from_env`` returns None -> pure
  deterministic behaviour (no network, no change).
* Strict timeout. Any transport error / timeout / hallucinated code degrades
  to None, so the pipeline falls back to a targeted clarify. The LLM never
  blocks the happy path and never overrides the deterministic hard-guards.
* Output is constrained to the real catalog codes and validated against
  ``CATALOG_BY_CODE`` — the model cannot invent categories.
* Category-only (smallest possible output) → fastest. Budget stays fully
  deterministic (already near-perfect and trap-safe).

Identity / persona
------------------
``GUEST_PERSONA_RULES`` is the single source of truth for how any guest-facing
LLM must behave: always the Taksitlio finance assistant, consistent answers,
and it must NEVER disclose that it is an AI / language model / which vendor —
identity questions get the fixed Taksitlio message (handled deterministically
in the pipeline, and reinforced here for any future chat use).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Mapping, Optional

from taksitlio.guest.need_extraction import CATALOG_BY_CODE, CategoryHit, normalize


# --- Persona / guardrails (reused by any guest-facing LLM) --------------------
GUEST_PERSONA_RULES = (
    "Sen Taksitlio'nun alışveriş finansmanı asistanısın. Kullanıcıya ihtiyacına "
    "ve bütçesine en uygun banka/marka finansman kampanyalarını bulursun. "
    "KİMLİK KURALI: Hiçbir koşulda yapay zeka, dil modeli, bot, hangi şirket/model "
    "olduğunu SÖYLEME. 'Sen kimsin / yapay zeka mısın / hangi modelsin' gibi "
    "sorulara yalnızca şu cevabı ver: 'Ben Taksitlio'nun alışveriş asistanıyım; "
    "sana en uygun kampanyaları bulmak için buradayım.' "
    "Türkçe, kısa ve tutarlı konuş. Sadece alışveriş/finansman kapsamında kal; "
    "hava durumu, siyaset, çeviri, genel sohbet gibi konulara girme."
)

# Shown by the client the moment the user sends, so the slow (LLM) path never
# feels frozen. Exported so the mobile app uses the exact same wording.
SEARCHING_TEXT = "Sana en uygun kampanyaları arıyorum… 🔎"

# Fixed identity answer — the bot must never reveal it is an AI / model / vendor.
IDENTITY_TEXT = (
    "Ben Taksitlio'nun alışveriş asistanıyım 👋 Sana ihtiyacına ve bütçene en "
    "uygun banka ve marka kampanyalarını bulmak için buradayım. Ne almak "
    "istediğini ve bütçeni yazman yeterli."
)

# Compact catalog list for the classifier prompt: "1=Cep Telefonu; 2=..."
_CATALOG_LINE = "; ".join(f"{code}={name}" for code, (name, _fam) in CATALOG_BY_CODE.items())

_CATEGORY_SYSTEM_PROMPT = (
    "Görevin: Türkçe bir alışveriş cümlesindeki ÜRÜNÜ aşağıdaki katalog "
    "kategorilerinden BİRİNE eşlemek.\n"
    f"Kategoriler: {_CATALOG_LINE}.\n"
    "Kurallar:\n"
    "- Sadece tek satır minified JSON döndür: {\"category_code\":\"<kod>\"} "
    "veya ürün belirsizse {\"category_code\":null}.\n"
    "- Kod yalnızca yukarıdaki listeden olabilir; asla uydurma.\n"
    "- Cümlede olmayan bilgiyi ekleme, açıklama/markdown yazma.\n"
    + GUEST_PERSONA_RULES
)

_JSON_OBJ = re.compile(r"\{.*?\}", re.DOTALL)


class GuestCategoryResolverLLM:
    """Small OpenAI-compatible model that maps free text → catalog category code.

    Consulted only on deterministic miss. Fail-open to None on any error.
    """

    name = "guest_category_llm"

    def __init__(
        self,
        *,
        base_url: str,
        model_reference: str,
        timeout_ms: int = 800,
        api_key: Optional[str] = None,
        chat_path: str = "/v1/chat/completions",
        max_output_tokens: int = 24,
        client: Any = None,
        cache_size: int = 1024,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("GuestCategoryResolverLLM base_url empty")
        if not model_reference or not model_reference.strip():
            raise ValueError("GuestCategoryResolverLLM model_reference empty")
        self._base_url = base_url.rstrip("/")
        self._model = model_reference
        self._timeout_s = max(timeout_ms / 1000.0, 0.2)
        self._api_key = api_key
        self._chat_path = chat_path if chat_path.startswith("/") else f"/{chat_path}"
        self._max_output_tokens = max_output_tokens
        self._client = client  # httpx.AsyncClient; lazily created if None
        self._owns_client = client is None
        self._cache: "dict[str, Optional[CategoryHit]]" = {}
        self._cache_size = cache_size
        self.last_latency_ms: Optional[float] = None  # wall time of the last HTTP call

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> Optional["GuestCategoryResolverLLM"]:
        """Build from GUEST_FAST_* (falling back to FAST_*), or None if unset.

        Absent config → None → pure deterministic guest (no silent stub).
        """
        env = env if env is not None else os.environ
        base = env.get("GUEST_FAST_BASE_URL") or env.get("FAST_PROVIDER_BASE_URL")
        model = env.get("GUEST_FAST_MODEL") or env.get("FAST_MODEL_REFERENCE")
        if not base or not model:
            return None
        timeout = int(env.get("GUEST_FAST_TIMEOUT_MS") or env.get("FAST_TIMEOUT_MS") or 800)
        api_key = env.get("GUEST_FAST_API_KEY") or env.get("FAST_API_KEY")
        return cls(
            base_url=base,
            model_reference=model,
            timeout_ms=timeout,
            api_key=api_key,
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    # -- resolution -----------------------------------------------------------
    async def resolve(self, utterance: str) -> Optional[CategoryHit]:
        key = normalize(utterance)
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]
        try:
            code = await self._classify(utterance)
        except Exception:  # noqa: BLE001 — fail-open, never block the pipeline
            return None  # transient: do not cache
        hit = self._hydrate(code)
        self._cache_put(key, hit)  # cache validated result incl. None (unresolvable)
        return hit

    def _hydrate(self, code: Optional[str]) -> Optional[CategoryHit]:
        if not code:
            return None
        code = str(code).strip()
        if code not in CATALOG_BY_CODE:  # reject hallucinated / off-catalog codes
            return None
        name, family = CATALOG_BY_CODE[code]
        return CategoryHit(category_code=code, display_name=name, family=family, confidence=0.7)

    def _cache_put(self, key: str, hit: Optional[CategoryHit]) -> None:
        if len(self._cache) >= self._cache_size:
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[key] = hit

    @staticmethod
    def preview_messages(utterance: str) -> list[dict[str, str]]:
        """Exactly the messages that would be sent — for prompt review / logging."""
        return [
            {"role": "system", "content": _CATEGORY_SYSTEM_PROMPT},
            {"role": "user", "content": utterance},
        ]

    def request_body(self, utterance: str) -> dict[str, Any]:
        """The full request payload (inspectable before/while sending)."""
        return {
            "model": self._model,
            "temperature": 0.0,
            "max_tokens": self._max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": self.preview_messages(utterance),
        }

    async def _classify(self, utterance: str) -> Optional[str]:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                f"{self._base_url}{self._chat_path}",
                json=self.request_body(utterance),
                headers=headers,
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            payload = resp.json()
        finally:
            self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        return self._parse_code(payload)

    @staticmethod
    def _parse_code(payload: Any) -> Optional[str]:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        if not isinstance(content, str):
            return None
        match = _JSON_OBJ.search(content)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        code = obj.get("category_code")
        return str(code) if code not in (None, "", "null") else None
