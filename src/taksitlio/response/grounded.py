"""Grounded response generation + membership CTA."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from taksitlio.campaign.ranking import RankedCampaign
from taksitlio.model_gateway.gateway import ModelGateway, ModelGatewayError
from taksitlio.model_gateway.types import CompletionRequest, ModelProfile


@dataclass(frozen=True)
class ResponsePolicy:
    policy_code: str = "DEFAULT"
    max_campaigns_in_reply: int = 3
    require_grounding: bool = True
    allow_fabricated_prices: bool = False
    membership_cta_enabled: bool = True
    out_of_scope_message: str = (
        "Bu konuda yardımcı olamıyorum. Taksitlio ürün ve kampanya ihtiyaçlarınız için buradayım."
    )
    clarification_template: str = (
        "Daha iyi önerebilmem için netleştirmem gerekiyor: {question}"
    )


@dataclass(frozen=True)
class MembershipCTA:
    enabled: bool
    label: str
    url: str | None
    reason: str


@dataclass(frozen=True)
class GroundedReply:
    text: str
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    cta: MembershipCTA | None = None
    grounded: bool = True
    used_model: bool = False
    latency_ms: float = 0.0
    template_used: str | None = None


class ResponsePolicyProvider(Protocol):
    async def get(self, policy_code: str = "DEFAULT") -> ResponsePolicy: ...


class StaticResponsePolicyProvider:
    def __init__(self, policy: ResponsePolicy | None = None) -> None:
        self._policy = policy or ResponsePolicy()

    async def get(self, policy_code: str = "DEFAULT") -> ResponsePolicy:
        return self._policy


class PromptProvider(Protocol):
    async def get_active(self, prompt_code: str) -> str: ...


CLARIFICATION_QUESTIONS = {
    "device_type": "Telefon mu, bilgisayar mı, yoksa tablet mi arıyorsunuz?",
    "budget": "Yaklaşık bütçeniz nedir veya aylık ödemeyi ne kadar düşünüyorsunuz?",
    "category": "Hangi ürün kategorisine bakıyorsunuz?",
    "usage": "Daha çok ne için kullanacaksınız?",
}


class GroundedResponseGenerator:
    """
    Builds replies strictly from ranked campaign records.

    Uses RESPONSE_GENERATION model when available; otherwise deterministic template.
    Never invents prices or installment terms not present in grounding data.
    """

    def __init__(
        self,
        gateway: ModelGateway | None,
        response_profile: ModelProfile | None,
        prompts: PromptProvider | None,
        policies: ResponsePolicyProvider,
    ) -> None:
        self._gateway = gateway
        self._profile = response_profile
        self._prompts = prompts
        self._policies = policies

    async def clarify(
        self,
        question_intent: str | None,
        *,
        policy_code: str = "DEFAULT",
    ) -> GroundedReply:
        policy = await self._policies.get(policy_code)
        question = CLARIFICATION_QUESTIONS.get(
            question_intent or "",
            "İhtiyacınızı biraz daha açabilir misiniz?",
        )
        text = policy.clarification_template.format(question=question)
        return GroundedReply(text=text, grounded=True, template_used="clarification")

    async def out_of_scope(self, *, policy_code: str = "DEFAULT") -> GroundedReply:
        policy = await self._policies.get(policy_code)
        return GroundedReply(
            text=policy.out_of_scope_message,
            grounded=True,
            template_used="out_of_scope",
        )

    async def from_campaigns(
        self,
        need_profile: Mapping[str, Any],
        ranked: Sequence[RankedCampaign],
        *,
        policy_code: str = "DEFAULT",
    ) -> GroundedReply:
        policy = await self._policies.get(policy_code)
        top = list(ranked)[: policy.max_campaigns_in_reply]
        grounding = [r.campaign.to_grounding_dict() for r in top]
        cta = _build_cta(top, policy)

        if not top:
            text = (
                "Bu ihtiyaca uygun aktif bir kampanya bulamadım. "
                "Bütçeyi veya ürün tercihini biraz değiştirebilir miyiz?"
            )
            return GroundedReply(
                text=text,
                campaigns=[],
                cta=cta,
                grounded=True,
                template_used="no_campaigns",
            )

        if self._gateway and self._profile and self._prompts:
            try:
                return await self._llm_reply(need_profile, grounding, cta, policy)
            except (ModelGatewayError, KeyError, Exception):
                pass

        return GroundedReply(
            text=_template_reply(need_profile, top, cta),
            campaigns=grounding,
            cta=cta,
            grounded=True,
            used_model=False,
            template_used="deterministic",
        )

    async def _llm_reply(
        self,
        need_profile: Mapping[str, Any],
        grounding: list[dict[str, Any]],
        cta: MembershipCTA | None,
        policy: ResponsePolicy,
    ) -> GroundedReply:
        assert self._gateway and self._profile and self._prompts
        system = await self._prompts.get_active("GROUNDED_RESPONSE")
        if policy.require_grounding:
            system += (
                "\nSadece verilen kampanya JSON alanlarını kullan. "
                "Olmayan fiyat/taksit uydurma."
            )
        user = (
            f"İhtiyaç profili:\n{need_profile}\n\n"
            f"Kampanyalar (grounding):\n{grounding}\n\n"
            f"CTA: {cta.label if cta and cta.enabled else 'yok'}"
        )
        result = await self._gateway.complete(
            self._profile,
            CompletionRequest(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=self._profile.max_output_tokens,
                temperature=float(self._profile.temperature),
                timeout_ms=self._profile.timeout_ms,
            ),
        )
        text = result.content.strip()
        if cta and cta.enabled and cta.label not in text:
            text = f"{text}\n\n{cta.label}" + (f": {cta.url}" if cta.url else "")
        return GroundedReply(
            text=text,
            campaigns=grounding,
            cta=cta,
            grounded=True,
            used_model=True,
            latency_ms=result.latency_ms,
            template_used="llm",
        )


def _build_cta(
    ranked: Sequence[RankedCampaign],
    policy: ResponsePolicy,
) -> MembershipCTA:
    if not policy.membership_cta_enabled:
        return MembershipCTA(False, "", None, "disabled_by_policy")
    for item in ranked:
        camp = item.campaign
        if camp.membership_required:
            return MembershipCTA(
                enabled=True,
                label=camp.membership_cta_label or "Taksitlio'ya üye ol",
                url=camp.membership_cta_url,
                reason="membership_required",
            )
    if ranked:
        camp = ranked[0].campaign
        return MembershipCTA(
            enabled=True,
            label=camp.membership_cta_label or "Taksitlio'ya üye ol",
            url=camp.membership_cta_url,
            reason="default_cta",
        )
    return MembershipCTA(
        enabled=True,
        label="Taksitlio'ya üye ol",
        url=None,
        reason="fallback_cta",
    )


def _template_reply(
    need_profile: Mapping[str, Any],
    ranked: Sequence[RankedCampaign],
    cta: MembershipCTA | None,
) -> str:
    need = str(need_profile.get("need_description") or "ihtiyacınız")
    lines = [f"{need} için uygun kampanyalar:"]
    for i, item in enumerate(ranked, start=1):
        c = item.campaign
        price_part = ""
        if c.list_price is not None:
            price_part = f" — {c.list_price:,.0f} {c.currency}".replace(",", ".")
        installment_part = ""
        if c.installment_count and c.monthly_payment is not None:
            installment_part = (
                f", {c.installment_count} taksit / aylık "
                f"{c.monthly_payment:,.0f} {c.currency}".replace(",", ".")
            )
        lines.append(f"{i}. {c.title}{price_part}{installment_part}")
        lines.append(f"   {c.summary}")
    if cta and cta.enabled:
        cta_line = cta.label
        if cta.url:
            cta_line = f"{cta.label}: {cta.url}"
        lines.append("")
        lines.append(cta_line)
    return "\n".join(lines)
