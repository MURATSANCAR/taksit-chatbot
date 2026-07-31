"""Grounded response generation + membership CTA (ADR-012 claim grounding)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from taksitlio.answer_integrity.facts import (
    EvidenceRef,
    Fact,
    FactEnvelope,
    FactType,
    build_fact_envelope,
)
from taksitlio.answer_integrity.pipeline import compose_grounded_answer
from taksitlio.answer_integrity.truth_status import (
    FieldTruthStatus,
    FinanceAvailability,
    ResponseOutcome,
)
from taksitlio.campaign.ranking import RankedCampaign
from taksitlio.answer_integrity.claim_validator import validate_claims
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
        "Bu konuda yardımcı olamıyorum. Yalnızca Taksitlio katalogundaki ürün ve "
        "taksit ihtiyaçlarınız için buradayım; sistemde olmayan bilgi veremem ve "
        "genel sohbet yapmam."
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
    cards: list[dict[str, Any]] = field(default_factory=list)
    phase: str | None = None
    cta: MembershipCTA | None = None
    grounded: bool = True
    used_model: bool = False
    latency_ms: float = 0.0
    template_used: str | None = None
    outcome: str | None = None
    fact_ids: tuple[str, ...] = ()
    claim_reasons: tuple[str, ...] = ()
    reason_explanation: str | None = None
    # Backward-compatible aliases used by earlier ADR-012 wiring attempts
    claim_validation_outcome: str | None = None
    response_fact_ids: tuple[str, ...] = ()
    allowed_facts: list[dict[str, Any]] = field(default_factory=list)


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
    Builds replies strictly from ranked campaign / product records.

    Optional LLM prose must pass Final Claim Validator; otherwise deterministic
    template is used (ADR-012).
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
        return GroundedReply(
            text=text,
            grounded=True,
            template_used="clarification",
            outcome=ResponseOutcome.PARTIALLY_ANSWERED.value,
        )

    async def out_of_scope(self, *, policy_code: str = "DEFAULT") -> GroundedReply:
        policy = await self._policies.get(policy_code)
        return GroundedReply(
            text=policy.out_of_scope_message,
            grounded=True,
            template_used="out_of_scope",
            outcome=ResponseOutcome.CANNOT_VERIFY.value,
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
        envelope = _envelope_from_campaign_grounding(grounding)

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
                outcome=ResponseOutcome.CANNOT_VERIFY.value,
            )

        template_text = _template_reply(need_profile, top, cta)
        template = GroundedReply(
            text=template_text,
            campaigns=grounding,
            cta=cta,
            grounded=True,
            used_model=False,
            template_used="deterministic",
            outcome=ResponseOutcome.ANSWERED.value,
            fact_ids=envelope.fact_ids(),
            response_fact_ids=envelope.fact_ids(),
            allowed_facts=envelope.allowed_facts(),
        )

        if self._gateway and self._profile and self._prompts:
            try:
                llm_reply = await self._llm_reply(
                    need_profile, grounding, cta, policy, envelope
                )
                check = validate_claims(llm_reply.text, envelope)
                if check.ok:
                    return GroundedReply(
                        text=llm_reply.text,
                        campaigns=grounding,
                        cta=cta,
                        grounded=True,
                        used_model=True,
                        latency_ms=llm_reply.latency_ms,
                        template_used="llm_claim_validated",
                        outcome=ResponseOutcome.ANSWERED.value,
                        fact_ids=envelope.fact_ids(),
                        response_fact_ids=envelope.fact_ids(),
                        allowed_facts=envelope.allowed_facts(),
                        claim_validation_outcome="PASSED",
                    )
                return GroundedReply(
                    text=template_text,
                    campaigns=grounding,
                    cta=cta,
                    grounded=True,
                    used_model=False,
                    latency_ms=llm_reply.latency_ms,
                    template_used="template_fallback_after_claim_fail",
                    outcome=ResponseOutcome.CLAIM_VALIDATION_FAILED.value,
                    fact_ids=envelope.fact_ids(),
                    response_fact_ids=envelope.fact_ids(),
                    allowed_facts=envelope.allowed_facts(),
                    claim_reasons=check.reasons,
                    claim_validation_outcome="CLAIM_VALIDATION_FAILED",
                )
            except (ModelGatewayError, KeyError, Exception):
                pass

        return template

    async def from_product_cards(
        self,
        need_profile: Mapping[str, Any],
        *,
        phase: str,
        cards: Sequence[Mapping[str, Any]],
        clarifications: Sequence[str] = (),
        policy_code: str = "DEFAULT",
        reason_codes: Sequence[str] = (),
        best_label_allowed: bool = False,
        ranking_label: str | None = None,
    ) -> GroundedReply:
        """Deterministic composer + claim gate — no invented prices/rates."""

        policy = await self._policies.get(policy_code)
        public_cards = [dict(c) for c in cards[: policy.max_campaigns_in_reply]]
        cta = MembershipCTA(
            enabled=policy.membership_cta_enabled,
            label="Taksitlio'ya üye ol",
            url=None,
            reason="product_path_cta",
        )
        if clarifications and not public_cards:
            text = " ".join(clarifications)
            return GroundedReply(
                text=text,
                cards=[],
                phase=phase,
                cta=cta,
                grounded=True,
                template_used="product_clarify",
                outcome=ResponseOutcome.PARTIALLY_ANSWERED.value,
            )
        if not public_cards:
            text = (
                "Bu ihtiyaca uygun ürün bulamadım. "
                "Bütçeyi veya ürün tercihini biraz değiştirebilir miyiz?"
            )
            return GroundedReply(
                text=text,
                cards=[],
                phase=phase,
                cta=cta,
                grounded=True,
                template_used="no_products",
                outcome=ResponseOutcome.CANNOT_VERIFY.value,
            )

        envelope = _envelope_from_product_cards(
            public_cards,
            phase=phase,
            reason_codes=reason_codes,
            ranking_label=ranking_label,
        )
        stock_status = str(public_cards[0].get("stock_status") or "") or None
        rate_type = None
        fees_total = 0.0
        finance = public_cards[0].get("best_finance")
        if isinstance(finance, Mapping):
            rate_type = str(finance.get("rate_type") or "") or None
            fees_total = float(finance.get("fees_total") or 0.0)

        answer = compose_grounded_answer(
            envelope,
            need_description=str(need_profile.get("need_description") or "ihtiyacınız"),
            cards=public_cards,
            best_label_allowed=best_label_allowed,
            stock_status=stock_status,
            rate_type=rate_type,
            fees_total=fees_total,
        )
        text = answer.text
        if cta and cta.enabled:
            text = f"{text}\n\n{cta.label}" + (f": {cta.url}" if cta.url else "")

        return GroundedReply(
            text=text,
            cards=public_cards,
            phase=phase,
            cta=cta,
            grounded=answer.grounded,
            used_model=answer.used_model,
            template_used=answer.template_used,
            outcome=answer.outcome.value,
            fact_ids=answer.fact_ids,
            response_fact_ids=answer.fact_ids,
            allowed_facts=list(answer.allowed_facts),
            claim_reasons=answer.claim_reasons,
            reason_explanation=answer.reason_explanation,
            claim_validation_outcome=(
                "CLAIM_VALIDATION_FAILED"
                if answer.outcome is ResponseOutcome.CLAIM_VALIDATION_FAILED
                else "PASSED"
            ),
        )

    async def _llm_reply(
        self,
        need_profile: Mapping[str, Any],
        grounding: list[dict[str, Any]],
        cta: MembershipCTA | None,
        policy: ResponsePolicy,
        envelope: FactEnvelope,
    ) -> GroundedReply:
        assert self._gateway and self._profile and self._prompts
        system = await self._prompts.get_active("GROUNDED_RESPONSE")
        if policy.require_grounding:
            system += (
                "\nSadece verilen allowed_facts ve kampanya JSON alanlarını kullan. "
                "Olmayan fiyat/taksit/banka uydurma."
            )
        user = (
            f"İhtiyaç profili:\n{need_profile}\n\n"
            f"allowed_facts:\n{envelope.allowed_facts()}\n\n"
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
            fact_ids=envelope.fact_ids(),
            response_fact_ids=envelope.fact_ids(),
            allowed_facts=envelope.allowed_facts(),
        )


def _envelope_from_campaign_grounding(
    grounding: Sequence[Mapping[str, Any]],
) -> FactEnvelope:
    facts: list[Fact] = []
    institutions: list[str] = []
    for i, g in enumerate(grounding):
        cid = str(g.get("id") or g.get("campaign_id") or f"camp_{i}")
        if g.get("list_price") is not None:
            facts.append(
                Fact(
                    fact_id=f"price_{cid}",
                    fact_type=FactType.PRICE,
                    value=f"{g['list_price']} {g.get('currency') or 'TRY'}",
                    truth_status=FieldTruthStatus.SOURCE_PROVIDED,
                    evidence=EvidenceRef(
                        price_snapshot_id=str(g.get("price_snapshot_id") or cid)
                    ),
                )
            )
        if g.get("monthly_payment") is not None:
            facts.append(
                Fact(
                    fact_id=f"pay_{cid}",
                    fact_type=FactType.MONTHLY_PAYMENT,
                    value=f"{g['monthly_payment']} {g.get('currency') or 'TRY'}",
                    truth_status=FieldTruthStatus.SOURCE_PROVIDED,
                    evidence=EvidenceRef(
                        payment_calculation_id=str(
                            g.get("payment_calculation_id") or f"pay_{cid}"
                        )
                    ),
                )
            )
        if g.get("installment_count") is not None:
            facts.append(
                Fact(
                    fact_id=f"term_{cid}",
                    fact_type=FactType.TERM,
                    value=f"{g['installment_count']} months",
                    truth_status=FieldTruthStatus.SOURCE_PROVIDED,
                    evidence=EvidenceRef(
                        rate_snapshot_id=str(
                            g.get("rate_snapshot_id")
                            or g.get("campaign_version_id")
                            or cid
                        ),
                        campaign_version_id=str(g.get("campaign_version_id") or cid),
                    ),
                    metadata={"term_months": int(g["installment_count"])},
                )
            )
        bank = g.get("bank_name") or g.get("institution_display_name")
        if bank:
            institutions.append(str(bank))
            facts.append(
                Fact(
                    fact_id=f"inst_{cid}",
                    fact_type=FactType.INSTITUTION,
                    value=str(bank),
                    truth_status=FieldTruthStatus.VERIFIED,
                    evidence=EvidenceRef(
                        merchant_finance_agreement_id=str(
                            g.get("merchant_finance_agreement_id") or f"agr_{cid}"
                        )
                    ),
                )
            )
    return build_fact_envelope(
        facts,
        institution_names=institutions,
        finance_availability=FinanceAvailability.AVAILABLE,
    )


def _envelope_from_product_cards(
    cards: Sequence[Mapping[str, Any]],
    *,
    phase: str,
    reason_codes: Sequence[str] = (),
    ranking_label: str | None = None,
) -> FactEnvelope:
    facts: list[Fact] = []
    institutions: list[str] = []
    merchants: list[str] = []
    product_ids: list[str] = []
    for i, card in enumerate(cards):
        pid = str(card.get("product_id") or card.get("offer_id") or f"p_{i}")
        product_ids.append(pid)
        name = str(card.get("display_name") or "")
        if name:
            facts.append(
                Fact(
                    fact_id=f"name_{pid}",
                    fact_type=FactType.DISPLAY_NAME,
                    value=name,
                    truth_status=FieldTruthStatus.VERIFIED,
                )
            )
        price = card.get("price")
        currency = card.get("currency") or "TRY"
        price_snap = card.get("price_snapshot_id") or card.get("offer_snapshot_id")
        if price is not None and price_snap:
            facts.append(
                Fact(
                    fact_id=f"price_{pid}",
                    fact_type=FactType.PRICE,
                    value=f"{price} {currency}",
                    truth_status=FieldTruthStatus.VERIFIED,
                    evidence=EvidenceRef(price_snapshot_id=str(price_snap)),
                    metadata={"product_id": pid},
                )
            )
        stock = card.get("stock_status")
        stock_snap = card.get("stock_snapshot_id")
        if stock and stock_snap:
            facts.append(
                Fact(
                    fact_id=f"stock_{pid}",
                    fact_type=FactType.STOCK,
                    value=str(stock),
                    truth_status=(
                        FieldTruthStatus.VERIFIED
                        if stock == "AVAILABLE"
                        else FieldTruthStatus.SOURCE_PROVIDED
                    ),
                    evidence=EvidenceRef(stock_snapshot_id=str(stock_snap)),
                )
            )
        merchant = card.get("merchant")
        if isinstance(merchant, Mapping) and merchant.get("display_name"):
            merchants.append(str(merchant["display_name"]))
        finance = card.get("best_finance") if phase == "FINANCE_ENRICHED" else None
        if isinstance(finance, Mapping):
            monthly = finance.get("monthly_payment")
            term = finance.get("term_months")
            pay_id = finance.get("payment_calculation_id")
            rate_id = finance.get("rate_snapshot_id")
            camp_id = finance.get("campaign_version_id")
            agr_id = finance.get("merchant_finance_agreement_id")
            inst = finance.get("institution_display_name")
            if monthly is not None and pay_id:
                facts.append(
                    Fact(
                        fact_id=f"pay_{pid}",
                        fact_type=FactType.MONTHLY_PAYMENT,
                        value=f"{monthly} {currency}",
                        truth_status=FieldTruthStatus.CALCULATED_ESTIMATE,
                        evidence=EvidenceRef(payment_calculation_id=str(pay_id)),
                    )
                )
            if term is not None and rate_id:
                facts.append(
                    Fact(
                        fact_id=f"term_{pid}",
                        fact_type=FactType.TERM,
                        value=f"{term} months",
                        truth_status=FieldTruthStatus.VERIFIED,
                        evidence=EvidenceRef(rate_snapshot_id=str(rate_id)),
                    )
                )
            if inst and agr_id:
                institutions.append(str(inst))
                facts.append(
                    Fact(
                        fact_id=f"inst_{pid}",
                        fact_type=FactType.INSTITUTION,
                        value=str(inst),
                        truth_status=FieldTruthStatus.VERIFIED,
                        evidence=EvidenceRef(
                            merchant_finance_agreement_id=str(agr_id),
                            campaign_version_id=str(camp_id) if camp_id else None,
                        ),
                    )
                )
    return build_fact_envelope(
        facts,
        finance_availability=(
            FinanceAvailability.AVAILABLE
            if any(f.fact_type is FactType.MONTHLY_PAYMENT for f in facts)
            else FinanceAvailability.UNAVAILABLE
        ),
        ranking_label=ranking_label,
        ranking_winner_product_id=product_ids[0] if product_ids else None,
        reason_codes=reason_codes,
        institution_names=institutions,
        merchant_names=merchants,
        product_ids=product_ids,
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
