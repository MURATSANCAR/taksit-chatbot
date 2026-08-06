"""
GUEST CAMPAIGN-ONLY PIPELINE (production final)
===============================================
Loginsiz conversion — ürün listesi YOK.

  OPENING → extract kategori+bütçe → CLARIFY | rank 1-2 kampanya → CTA
  Refinement: daha ucuz / uzun vade / başka banka
  Lexical guard: buzdolabı ≠ MOBILE_PHONE
  Sticky reset: family değişince bütçe/kategori temizlenir
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lexical category (inline fallback if module path differs)
# ---------------------------------------------------------------------------

try:
    from taksitlio.query_understanding.lexical_category_guard import (
        detect_lexical_category,
        guard_resolved_category,
    )
except ImportError:  # pragma: no cover
    detect_lexical_category = None  # type: ignore
    guard_resolved_category = None  # type: ignore


from taksitlio.guest.need_extraction import (
    extract_need as _extract_need_det,
    extract_profile as _extract_profile_det,
    resolve_category as _resolve_category_det,
)


def _lexical(text: str) -> Optional[Any]:
    # Deterministic 28-category lexicon first (covers full catalog). Falls back
    # to the legacy narrow guard only if the lexicon misses.
    hit = _resolve_category_det(text)
    if hit is not None:
        return hit
    if detect_lexical_category is None:
        return _fallback_lexical(text)
    return detect_lexical_category(text)


def _fallback_lexical(text: str) -> Optional[Any]:
    from types import SimpleNamespace

    patterns = [
        (r"\b(buzdolab[ıi]|buz\s*dolab)\b", "WHITE_GOODS", "Buzdolabı"),
        (r"\b(çamaş[ıi]r|cama[sş]ir)\b", "WHITE_GOODS", "Çamaşır Makinesi"),
        (r"\b(bula[sş][ıi]k)\b", "WHITE_GOODS", "Bulaşık Makinesi"),
        (r"\b(beyaz\s*e[sş]ya)\b", "WHITE_GOODS", "Beyaz Eşya"),
        (r"\b(klima)\b", "AIR_CONDITIONER", "Klima"),
        (r"\b(televizyon|\btv\b)\b", "TV", "Televizyon"),
        (r"\b(laptop|notebook|bilgisayar|macbook)\b", "COMPUTER", "Bilgisayar"),
        (r"\b(tablet|ipad)\b", "TABLET", "Tablet"),
        (
            r"(?:cep\s*telefonu?|ak[ıi]ll[ıi]\s*telefonu?"
            r"|\btelefon(?:u|um|lar(?:ı|i)?)?\b|\biphone\b)",
            "MOBILE_PHONE",
            "Cep Telefonu",
        ),
    ]
    for pat, code, name in patterns:
        if re.search(pat, text or "", re.I):
            return SimpleNamespace(
                family=code,
                category_hint=code,
                display_name=name,
                confidence=0.98,
            )
    return None


OPENING_TEXT = (
    "Merhaba! 👋 Taksitlio'ya hoş geldin.\n\n"
    "Senin için ihtiyaç analizi yapayım mı?\n"
    "Ne almak istediğini ve bütçeni kendi cümlelerinle yazman yeterli "
    "(örnek: \"cep telefonu alacağım, bütçem 40 bin TL civarı\")."
)

CLARIFY_BUDGET = (
    "Anladım. Uygun finansman kampanyalarını bulabilmem için "
    "yaklaşık bütçeni de yazar mısın?\n"
    "(örnek: 25.000 TL veya 40 bin civarı)"
)

CLARIFY_CATEGORY = (
    "Bütçeni aldım. Hangi ürün için bakıyorsun?\n"
    "(cep telefonu, buzdolabı, laptop, tablet, klima…)"
)

NO_CAMPAIGN = (
    "Şu an bu kategori ve bütçeye uyan aktif bir finansman kampanyası bulamadım.\n"
    "Üye olursan güncel kampanya havuzunu birlikte tarayabiliriz."
)

CTA = {
    "label": "Üye ol, kampanyadan yararlan",
    "action": "NAVIGATE_REGISTER",
    "require_membership": True,
    "style": "primary",
}

_BUDGET_RE = re.compile(
    r"(?:bütçe(?:m|si)?\s*(?:yaklaşık|civarı|kadar)?\s*)?"
    r"(\d+(?:[.,]\d{3})*|\d+)\s*(bin)?\s*"
    r"(?:[-–—]\s*(\d+(?:[.,]\d{3})*|\d+)\s*(bin)?)?\s*(?:tl|lira|₺)?",
    re.I,
)

_REFINE_CHEAPER = re.compile(
    r"\b(daha\s+ucuz|daha\s+uygun|en\s+ucuz|ucuzlat|düşük\s+(?:faiz|oran|kar))\b",
    re.I,
)
_REFINE_LONGER = re.compile(r"\b(daha\s+uzun\s+vade|uzun\s+vade|\d+\s*ay\s+olsun)\b", re.I)
_REFINE_OTHER_BANK = re.compile(
    r"\b(başka\s+banka|diğer\s+banka|albaraka\s+olmasın|kuveyt\s+olmasın)\b", re.I
)
_REFINE_MORE = re.compile(r"\b(daha\s+fazla|başka\s+seçenek|alternatif)\b", re.I)
_KAMPANYA = re.compile(r"\b(kampanya\s+var\s+m[ıi]|kampanyalar[ıi]?|finansman)\b", re.I)


class GuestPhase(str, Enum):
    OPENING = "OPENING"
    AWAITING_NEED = "AWAITING_NEED"
    CLARIFY = "CLARIFY"
    COMPLETED = "COMPLETED"
    REFINING = "REFINING"
    SAFE_FAILURE = "SAFE_FAILURE"


@dataclass
class GuestContext:
    phase: str = GuestPhase.OPENING.value
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    budget_value: Optional[float] = None
    campaign_ids: list[Any] = field(default_factory=list)
    lexical_family: Optional[str] = None
    prefer_cheaper: bool = False
    prefer_longer: bool = False
    exclude_banks: list[str] = field(default_factory=list)
    include_banks: list[str] = field(default_factory=list)
    tenure_months: Optional[int] = None


def _parse_budget(text: str) -> Optional[float]:
    # Delegates to the hardened parser (ignores model/spec numbers, supports
    # "40k", "kırk bin", ranges). Legacy _BUDGET_RE retained for reference only.
    from taksitlio.guest.need_extraction import parse_budget

    return parse_budget(text)


def _extract_need(text: str) -> dict[str, Any]:
    lexical = _lexical(text)
    budget = _parse_budget(text)
    return {
        "category_code": getattr(lexical, "category_hint", None) if lexical else None,
        "category_name": getattr(lexical, "display_name", None) if lexical else None,
        "lexical_family": getattr(lexical, "family", None) if lexical else None,
        "budget_value": budget,
        "has_product_noun": lexical is not None,
    }


class CampaignOnlyGuestPipeline:
    def __init__(
        self,
        state_manager: Any,
        campaign_repo: Any,
        *,
        ranker: Any = None,
        eligibility: Any = None,
        max_campaigns: int = 2,
    ) -> None:
        self._state = state_manager
        self._campaigns = campaign_repo
        self._ranker = ranker
        self._eligibility = eligibility
        self._max = max_campaigns

    async def start(
        self,
        *,
        locale: str = "tr-TR",
        session_id: Optional[UUID] = None,
    ) -> dict[str, Any]:
        from taksitlio.conversation_state.domain import Actor, ActorType

        state = await self._state.create_session(
            locale=locale,
            actor=Actor(type=ActorType.ANONYMOUS),
            session_id=session_id,
            idempotency_key=str(uuid.uuid4()),
        )
        cas = await self._state.apply_model_update(
            state.session_id,
            expected_revision=state.revision,
            patch={
                "operation": "SET",
                "path": "/resolved_context/guest",
                "value": {
                    "phase": GuestPhase.OPENING.value,
                    "mode": "CAMPAIGN_ONLY",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            idempotency_key=str(uuid.uuid4()),
            client_message_id=str(uuid.uuid4()),
            client_sequence=None,
        )
        return self._result(
            session_id=str(state.session_id),
            phase=GuestPhase.OPENING.value,
            revision=getattr(cas, "revision", state.revision + 1),
            reply=OPENING_TEXT,
            campaigns=[],
            cta=None,
            diagnostics={"entry": "campaign_only_opening", "product_path": False},
        )

    async def handle(
        self,
        *,
        session_id: str,
        utterance: str,
        expected_revision: int = 0,
        client_message_id: Optional[str] = None,
        client_sequence: Optional[int] = None,
        locale: str = "tr-TR",
    ) -> dict[str, Any]:
        sid = UUID(str(session_id)) if not isinstance(session_id, UUID) else session_id
        try:
            state = await self._state.get_session(sid)
        except Exception:
            # Mobile often sends a client-generated UUID before server session exists.
            # Create under that id, then process this utterance (do not drop need).
            started = await self.start(locale=locale, session_id=sid)
            return await self.handle(
                session_id=started["session_id"],
                utterance=utterance,
                expected_revision=int(started.get("revision") or 1),
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                locale=locale,
            )

        # Mobile may omit revision; use live CAS base.
        if not expected_revision:
            expected_revision = int(getattr(state, "revision", 0) or 0)

        ctx = self._load_ctx(state)
        need = _extract_need(utterance)
        low = (utterance or "").casefold()

        # Rich profile: honor multi-constraint preferences from the FIRST turn
        # (cheaper / longer vade / bank exclude-include / tenure) with no LLM.
        profile = _extract_profile_det(utterance)
        if profile.prefer_cheaper:
            ctx.prefer_cheaper = True
        if profile.prefer_longer:
            ctx.prefer_longer = True
        if profile.tenure_months:
            ctx.tenure_months = profile.tenure_months
        for _b in profile.exclude_banks:
            if _b not in ctx.exclude_banks:
                ctx.exclude_banks.append(_b)
        for _b in profile.include_banks:
            if _b not in ctx.include_banks:
                ctx.include_banks.append(_b)

        # --- Refinement flags (after COMPLETED) ---
        if ctx.phase in (GuestPhase.COMPLETED.value, GuestPhase.REFINING.value):
            if _REFINE_CHEAPER.search(utterance or ""):
                ctx.prefer_cheaper = True
            if _REFINE_LONGER.search(utterance or ""):
                ctx.prefer_longer = True
            if _REFINE_OTHER_BANK.search(utterance or ""):
                if "albaraka" in low:
                    ctx.exclude_banks.append("albaraka")
                if "kuveyt" in low:
                    ctx.exclude_banks.append("kuveyt")
                if not ctx.exclude_banks:
                    ctx.exclude_banks.append("__diversify__")

        # --- Sticky reset on family change ---
        if need["lexical_family"] and ctx.lexical_family:
            if need["lexical_family"] != ctx.lexical_family:
                ctx.budget_value = None
                ctx.campaign_ids = []
                ctx.category_code = None
                ctx.category_name = None
                ctx.prefer_cheaper = False
                ctx.prefer_longer = False
                ctx.exclude_banks = []

        if need["lexical_family"]:
            ctx.lexical_family = need["lexical_family"]
        if need["category_code"]:
            code, name = need["category_code"], need["category_name"]
            if guard_resolved_category is not None:
                code, name, _ = guard_resolved_category(
                    utterance, need["category_code"], need["category_name"]
                )
            # Hard block: never keep MOBILE_PHONE if lexical is WHITE_GOODS etc.
            if need["lexical_family"] and need["lexical_family"] != "MOBILE_PHONE":
                if str(code).upper() in ("MOBILE_PHONE", "PHONE", "1", "CEP_TELEFONU"):
                    code = need["category_code"]
                    name = need["category_name"]
            ctx.category_code = code
            ctx.category_name = name
        if need["budget_value"]:
            ctx.budget_value = need["budget_value"]

        diag: dict[str, Any] = {
            "need": need,
            "ctx_category": ctx.category_code,
            "ctx_budget": ctx.budget_value,
            "lexical_family": ctx.lexical_family,
            "product_path": False,
            "campaign_only": True,
            "secondary_categories": [
                (h.category_code, h.display_name) for h in profile.secondary
            ],
            "profile": {
                "prefer_cheaper": ctx.prefer_cheaper,
                "prefer_longer": ctx.prefer_longer,
                "tenure_months": ctx.tenure_months,
                "exclude_banks": ctx.exclude_banks,
                "include_banks": ctx.include_banks,
                "budget_type": profile.budget_type,
                "is_complex": profile.is_complex,
            },
        }

        # Membership FAQ
        if any(x in low for x in ("nasıl üye", "üye ol", "üyelik")):
            return await self._finish(
                sid,
                expected_revision,
                client_message_id,
                client_sequence,
                ctx,
                phase=ctx.phase or GuestPhase.AWAITING_NEED.value,
                reply=(
                    "Üyelik ücretsiz. “Üye ol, kampanyadan yararlan” ile kayıt olabilirsin; "
                    "sonra başvuru ve ödeme planını birlikte yönetiriz."
                ),
                campaigns=[],
                cta=CTA,
                diag=diag,
            )

        # "kampanya var mı" with existing ctx → re-rank
        if _KAMPANYA.search(utterance or "") and ctx.category_code and ctx.budget_value:
            ranked = await self._rank_campaigns(ctx)
            return await self._recommend(
                sid,
                expected_revision,
                client_message_id,
                client_sequence,
                ctx,
                ranked,
                diag,
            )

        # Clarify
        if not ctx.category_code and not ctx.budget_value:
            return await self._finish(
                sid,
                expected_revision,
                client_message_id,
                client_sequence,
                ctx,
                phase=GuestPhase.CLARIFY.value,
                reply=(
                    "Hem kategoriyi hem yaklaşık bütçeyi yazarsan "
                    "en uygun 1-2 finansman kampanyasını getireyim.\n\n"
                    "Örnek: \"buzdolabı, 30 bin TL\" veya \"cep telefonu, 40 bin\""
                ),
                campaigns=[],
                cta=None,
                diag=diag,
            )
        if not ctx.budget_value:
            return await self._finish(
                sid,
                expected_revision,
                client_message_id,
                client_sequence,
                ctx,
                phase=GuestPhase.CLARIFY.value,
                reply=CLARIFY_BUDGET,
                campaigns=[],
                cta=None,
                diag=diag,
            )
        if not ctx.category_code:
            return await self._finish(
                sid,
                expected_revision,
                client_message_id,
                client_sequence,
                ctx,
                phase=GuestPhase.CLARIFY.value,
                reply=CLARIFY_CATEGORY,
                campaigns=[],
                cta=None,
                diag=diag,
            )

        ranked = await self._rank_campaigns(ctx)
        diag["ranked_count"] = len(ranked)
        return await self._recommend(
            sid,
            expected_revision,
            client_message_id,
            client_sequence,
            ctx,
            ranked,
            diag,
        )

    async def _recommend(
        self,
        sid,
        expected_revision,
        client_message_id,
        client_sequence,
        ctx: GuestContext,
        ranked: list[dict[str, Any]],
        diag: dict[str, Any],
    ) -> dict[str, Any]:
        if not ranked:
            return await self._finish(
                sid,
                expected_revision,
                client_message_id,
                client_sequence,
                ctx,
                phase=GuestPhase.COMPLETED.value,
                reply=NO_CAMPAIGN,
                campaigns=[],
                cta=CTA,
                diag=diag,
            )

        budget_txt = f"{ctx.budget_value:,.0f} TL".replace(",", ".")
        cat = ctx.category_name or ctx.category_code or "ihtiyacın"
        intro = (
            f"{budget_txt} bütçene ve {cat} ihtiyacına göre "
            f"şu an en uygun {len(ranked)} finansman kampanyasını buldum:"
        )
        lines = [intro]
        for i, c in enumerate(ranked, 1):
            title = c.get("title") or c.get("summary") or "Kampanya"
            bank = c.get("brand") or c.get("bank") or ""
            rate = c.get("rate_text") or (c.get("attributes") or {}).get("rate_text") or ""
            part = f"{i}) {title}"
            if bank:
                part += f" — {bank}"
            if rate:
                part += f" ({rate})"
            lines.append(part)
        secondary = diag.get("secondary_categories") or []
        if secondary:
            names = ", ".join(n for _c, n in secondary if n)
            lines.append(
                f"\nAyrıca {names} için de kampanyalar var; "
                "üye olunca hepsini birlikte inceleyebiliriz."
            )
        lines.append(
            "\nBu kampanyalardan yararlanmak için üye olman yeterli. "
            "İstersen \"daha ucuz\", \"daha uzun vade\" veya \"başka banka\" yazabilirsin."
        )
        ctx.campaign_ids = [c.get("id") or c.get("campaign_code") for c in ranked]
        phase = (
            GuestPhase.REFINING.value
            if ctx.prefer_cheaper or ctx.prefer_longer or ctx.exclude_banks
            else GuestPhase.COMPLETED.value
        )
        return await self._finish(
            sid,
            expected_revision,
            client_message_id,
            client_sequence,
            ctx,
            phase=phase,
            reply="\n".join(lines),
            campaigns=ranked,
            cta=CTA,
            diag=diag,
        )

    async def _rank_campaigns(self, ctx: GuestContext) -> list[dict[str, Any]]:
        codes: list[str] = []
        if ctx.category_code:
            codes.append(str(ctx.category_code))
        fam = (ctx.lexical_family or ctx.category_code or "").upper()
        if fam in ("MOBILE_PHONE", "PHONE"):
            codes.extend(["1", "MOBILE_PHONE"])
        elif fam in ("WHITE_GOODS", "BEYAZ_ESYA"):
            codes.extend(["WHITE_GOODS", "BEYAZ_ESYA", "2"])
        elif fam == "TV":
            codes.extend(["TV", "3"])
        elif fam in ("COMPUTER", "LAPTOP"):
            codes.extend(["COMPUTER", "LAPTOP", "4"])
        codes = list(dict.fromkeys(codes))

        raw: list[Any] = []
        try:
            if hasattr(self._campaigns, "list_by_category_codes") and codes:
                raw = list(await self._campaigns.list_by_category_codes(codes, limit=50))
            # Do NOT cross-pollinate categories (WHITE_GOODS must never pull phone).
            if not raw and hasattr(self._campaigns, "list_active"):
                raw = list(await self._campaigns.list_active())
        except Exception as exc:
            logger.exception("campaign repo failed: %s", exc)
            return []

        items: list[dict[str, Any]] = []
        allowed_codes = {c.upper() for c in codes if c}
        # Family aliases for post-filter when list_active is used
        if fam in ("MOBILE_PHONE", "PHONE"):
            allowed_codes.update({"1", "MOBILE_PHONE", "PHONE"})
        elif fam in ("WHITE_GOODS", "BEYAZ_ESYA"):
            allowed_codes.update({"WHITE_GOODS", "BEYAZ_ESYA", "HOME_APPLIANCE", "2"})
        elif fam == "TV":
            allowed_codes.update({"TV", "3"})
        elif fam in ("COMPUTER", "LAPTOP"):
            allowed_codes.update({"COMPUTER", "LAPTOP", "4"})

        for c in raw:
            d = self._to_dict(c)
            if d.get("status") and str(d["status"]).upper() != "ACTIVE":
                continue
            camp_code = str(d.get("category_code") or "").upper()
            is_general = bool(d.get("general_finance")) or str(
                (d.get("attributes") or {}).get("scope") or ""
            ).upper() == "GENERAL"
            # General bank-finance offers (Albaraka/Kuveyt/GetirFinans genel
            # alışveriş finansmanı) apply to ANY category — only budget-gated.
            if (
                not is_general
                and allowed_codes
                and camp_code
                and camp_code not in allowed_codes
            ):
                # Hard: never show phone campaigns for non-phone family
                if fam and fam not in ("MOBILE_PHONE", "PHONE") and camp_code in (
                    "MOBILE_PHONE",
                    "PHONE",
                    "1",
                ):
                    continue
                if fam in ("MOBILE_PHONE", "PHONE") and camp_code not in allowed_codes:
                    continue
                if fam and fam not in ("MOBILE_PHONE", "PHONE"):
                    continue
            b = ctx.budget_value
            mx = d.get("max_budget")
            mn = d.get("min_budget")
            if b is not None:
                if mx is not None and b > float(mx) * 1.05:
                    continue
                if mn is not None and b < float(mn) * 0.5:
                    continue
            bank = str(d.get("brand") or d.get("bank") or "").lower()
            if ctx.exclude_banks and "__diversify__" not in ctx.exclude_banks:
                if any(x in bank for x in ctx.exclude_banks):
                    continue
            if ctx.include_banks and not any(x in bank for x in ctx.include_banks):
                continue
            items.append(d)

        # Drop already shown on "more / other bank"
        if ctx.exclude_banks or ctx.prefer_cheaper:
            shown = set(str(x) for x in (ctx.campaign_ids or []))
            if shown and len(items) > self._max:
                filtered = [
                    d
                    for d in items
                    if str(d.get("id") or d.get("campaign_code")) not in shown
                ]
                if filtered:
                    items = filtered

        def _rate_pct(d: dict[str, Any]) -> Optional[float]:
            txt = d.get("rate_text") or (d.get("attributes") or {}).get("rate_text") or ""
            m = re.search(r"%\s*(\d+(?:[.,]\d+)?)", str(txt))
            return float(m.group(1).replace(",", ".")) if m else None

        def score(d: dict[str, Any]) -> float:
            s = 0.5
            b = ctx.budget_value
            mn = d.get("min_budget")
            mx = d.get("max_budget")
            # Budget fit: comfortably-within-range beats a tight ceiling match.
            if b is not None:
                if mx is not None and float(b) > float(mx):
                    s -= 0.20
                elif mn is not None and float(b) < float(mn):
                    s -= 0.10
                else:
                    s += 0.30
            # Real numeric rate: lower %% is cheaper → higher score.
            rate = _rate_pct(d)
            if rate is not None:
                rate_score = max(0.0, 1.0 - rate / 5.0)
                s += (0.30 if ctx.prefer_cheaper else 0.15) * rate_score
            # Tenure: honor "uzun vade" and an explicit "<n> ay" target.
            inst = d.get("installment_count")
            if inst:
                if ctx.prefer_longer:
                    s += min(0.20, float(inst) / 60.0)
                if ctx.tenure_months:
                    s += 0.10 * max(0.0, 1.0 - abs(float(inst) - ctx.tenure_months) / 12.0)
            return s

        items.sort(key=score, reverse=True)
        out = items[: self._max]
        for d in out:
            d["score"] = round(score(d), 3)
        return out

    @staticmethod
    def _to_dict(c: Any) -> dict[str, Any]:
        if isinstance(c, dict):
            return dict(c)
        attrs = getattr(c, "attributes", None) or {}
        return {
            "id": getattr(c, "id", None),
            "campaign_code": getattr(c, "campaign_code", None),
            "title": getattr(c, "title", ""),
            "summary": getattr(c, "summary", ""),
            "brand": getattr(c, "brand", None),
            "bank": getattr(c, "brand", None),
            "min_budget": getattr(c, "min_budget", None),
            "max_budget": getattr(c, "max_budget", None),
            "rate_text": attrs.get("rate_text") if isinstance(attrs, dict) else None,
            "installment_count": getattr(c, "installment_count", None),
            "membership_required": getattr(c, "membership_required", True),
            "attributes": attrs,
            "status": getattr(c, "status", "ACTIVE"),
        }

    def _load_ctx(self, state: Any) -> GuestContext:
        guest = (getattr(state, "resolved_context", None) or {}).get("guest") or {}
        return GuestContext(
            phase=guest.get("phase") or GuestPhase.AWAITING_NEED.value,
            category_code=guest.get("category_code"),
            category_name=guest.get("category_name"),
            budget_value=guest.get("budget_value"),
            campaign_ids=list(guest.get("campaign_ids") or []),
            lexical_family=guest.get("lexical_family"),
            prefer_cheaper=bool(guest.get("prefer_cheaper")),
            prefer_longer=bool(guest.get("prefer_longer")),
            exclude_banks=list(guest.get("exclude_banks") or []),
            include_banks=list(guest.get("include_banks") or []),
            tenure_months=guest.get("tenure_months"),
        )

    async def _finish(
        self,
        sid: UUID,
        expected_revision: int,
        client_message_id: Optional[str],
        client_sequence: Optional[int],
        ctx: GuestContext,
        *,
        phase: str,
        reply: str,
        campaigns: list[dict[str, Any]],
        cta: Optional[dict[str, Any]],
        diag: dict[str, Any],
    ) -> dict[str, Any]:
        guest_val = {
            "phase": phase,
            "mode": "CAMPAIGN_ONLY",
            "category_code": ctx.category_code,
            "category_name": ctx.category_name,
            "budget_value": ctx.budget_value,
            "campaign_ids": ctx.campaign_ids,
            "lexical_family": ctx.lexical_family,
            "prefer_cheaper": ctx.prefer_cheaper,
            "prefer_longer": ctx.prefer_longer,
            "exclude_banks": ctx.exclude_banks,
            "include_banks": ctx.include_banks,
            "tenure_months": ctx.tenure_months,
        }
        cas = await self._state.apply_model_update(
            sid,
            expected_revision=expected_revision,
            patch={
                "operation": "SET",
                "path": "/resolved_context/guest",
                "value": guest_val,
            },
            idempotency_key=str(uuid.uuid4()),
            client_message_id=client_message_id or str(uuid.uuid4()),
            client_sequence=client_sequence,
        )
        return self._result(
            session_id=str(sid),
            phase=phase,
            revision=getattr(cas, "revision", expected_revision + 1),
            reply=reply,
            campaigns=campaigns,
            cta=cta,
            diagnostics=diag,
        )

    @staticmethod
    def _result(
        *,
        session_id: str,
        phase: str,
        revision: int,
        reply: str,
        campaigns: list[dict[str, Any]],
        cta: Optional[dict[str, Any]],
        diagnostics: dict[str, Any],
    ) -> dict[str, Any]:
        if campaigns:
            decision = "GUEST_RECOMMENDATION"
        elif phase == GuestPhase.CLARIFY.value:
            decision = "GUEST_CLARIFY"
        elif phase == GuestPhase.OPENING.value:
            decision = "GUEST_OPENING"
        else:
            decision = "GUEST_SAFE"
        return {
            "session_id": session_id,
            "phase": phase,
            "revision": revision,
            "reply": reply,
            "decision": decision,
            "campaigns": campaigns,
            "cards": [],  # HARD: never product cards
            "cta": cta,
            "membership_cta": cta,
            "diagnostics": {**diagnostics, "product_path": False, "campaign_only": True},
            "messages": (
                [{"role": "assistant", "content": reply, "type": "text"}]
                + [
                    {
                        "role": "assistant",
                        "type": "campaign_card",
                        "content": c.get("title") or "",
                        "card": c,
                    }
                    for c in campaigns
                ]
            ),
        }
