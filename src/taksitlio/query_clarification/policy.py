"""Clarification-first policy and question selection (ADR-011 §8–§11)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from taksitlio.query_understanding.gap_detector import GapAnalysis, Uncertainty
from taksitlio.query_understanding.fast_parser import FastParseResult


@dataclass(frozen=True)
class ClarificationOption:
    option_id: str
    label: str


@dataclass
class ClarificationQuestion:
    clarification_id: str
    field: str
    question_text: str
    question_signature: str
    options: list[ClarificationOption] = field(default_factory=list)
    allow_free_text: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "clarification_id": self.clarification_id,
            "field": self.field,
            "question_text": self.question_text,
            "question_signature": self.question_signature,
            "options": [{"option_id": o.option_id, "label": o.label} for o in self.options],
            "allow_free_text": self.allow_free_text,
        }


def _signature(field: str, question_text: str) -> str:
    raw = f"{field}:{question_text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def score_uncertainty(u: Uncertainty) -> float:
    return (
        float(u.expected_information_gain)
        + (0.25 if u.can_clarification_resolve else -1.0)
        + (0.1 if u.field in {"category", "product_type"} else 0.0)
    )


def select_best_uncertainty(
    gaps: GapAnalysis,
    *,
    already_asked: Sequence[str] = (),
) -> Optional[Uncertainty]:
    asked = set(already_asked)
    ranked = sorted(
        (u for u in gaps.uncertainties if u.can_clarification_resolve and u.field not in asked),
        key=score_uncertainty,
        reverse=True,
    )
    return ranked[0] if ranked else None


def build_clarification(
    uncertainty: Uncertainty,
    *,
    catalog_options: Optional[Sequence[dict[str, str]]] = None,
    include_undecided: bool = True,
) -> ClarificationQuestion:
    """Build a targeted question with at most 4 primary options from catalog."""

    options: list[ClarificationOption] = []
    if catalog_options:
        for item in list(catalog_options)[:4]:
            options.append(
                ClarificationOption(
                    option_id=str(item.get("id") or item.get("option_id")),
                    label=str(item.get("label") or item.get("display_name")),
                )
            )
    elif uncertainty.candidate_values:
        for val in list(uncertainty.candidate_values)[:4]:
            options.append(ClarificationOption(option_id=str(val), label=str(val)))

    field = uncertainty.field
    if field in {"category", "product_type"}:
        question = "Hangi ürün türünü tercih ediyorsunuz?"
        if not options:
            options = [
                ClarificationOption("phone", "Telefon"),
                ClarificationOption("tablet", "Tablet"),
                ClarificationOption("laptop", "Bilgisayar"),
            ]
        if include_undecided and len(options) < 4:
            options.append(ClarificationOption("undecided", "Kararsızım"))
    elif field == "merchant":
        question = "Ürünü hangi mağazadan almak istiyorsunuz?"
        if include_undecided and len(options) < 4:
            options.append(ClarificationOption("undecided", "Fark etmez"))
    elif field == "usage":
        question = "Daha çok hangi amaçla kullanacaksınız?"
        if not options:
            options = [
                ClarificationOption("education", "Okul ve ödev"),
                ClarificationOption("gaming", "Oyun"),
                ClarificationOption("both", "İkisi de"),
                ClarificationOption("undecided", "Henüz karar vermedim"),
            ]
    elif field == "budget":
        question = "Bütçenizin üst sınırı nedir?"
        options = options[:4]
    elif field == "payment_preference":
        question = "Aylık ödeme mi, toplam geri ödeme mi daha önemli?"
        if not options:
            options = [
                ClarificationOption("monthly", "Aylık ödeme"),
                ClarificationOption("total", "Toplam geri ödeme"),
                ClarificationOption("undecided", "Fark etmez"),
            ]
    else:
        question = f"{field} tercihini netleştirebilir misiniz?"

    # Cap at 4 primary + ensure undecided if needed and room
    options = options[:4]
    cid = str(uuid.uuid4())
    return ClarificationQuestion(
        clarification_id=cid,
        field=field,
        question_text=question,
        question_signature=_signature(field, question),
        options=options,
    )


def should_ask_clarification(
    *,
    gaps: GapAnalysis,
    clarification_count: int,
    max_per_session: int = 2,
    parse: Optional[FastParseResult] = None,
) -> bool:
    if clarification_count >= max_per_session:
        return False
    if gaps.confidence_band == "HIGH":
        return False
    if gaps.confidence_band == "LOW" and gaps.requires_llm and not gaps.clarification_viable:
        return False
    if not gaps.clarification_viable:
        return False
    # Single short answer can raise to HIGH?
    best = select_best_uncertainty(gaps)
    if best is None:
        return False
    return best.expected_information_gain >= 0.75


def apply_clarification_answer(
    parse: FastParseResult,
    *,
    field: str,
    selected_option_ids: Sequence[str],
    free_text: Optional[str] = None,
    option_labels: Optional[dict[str, str]] = None,
) -> FastParseResult:
    """Merge clarification into a new parse snapshot (immutable-ish copy)."""

    labels = option_labels or {}
    selected = list(selected_option_ids)
    text_bits = [labels.get(s, s) for s in selected]
    if free_text:
        text_bits.append(free_text)

    if field in {"category", "product_type"} and selected:
        from taksitlio.query_understanding.fast_parser import ResolvedEntityRef

        for sid in selected:
            if sid == "undecided":
                continue
            label = labels.get(sid, sid)
            parse.positive_categories.append(
                ResolvedEntityRef(
                    resolved_id=sid,
                    display_name=label,
                    match_type="CLARIFICATION",
                    confidence=0.95,
                    required=True,
                )
            )
        parse.confidence = max(parse.confidence, 0.91)
        parse.requires_llm = False
    elif field == "usage" and selected:
        mapping = {
            "education": "education",
            "gaming": "gaming",
            "both": "education",
        }
        for sid in selected:
            if sid in mapping:
                parse.usage_contexts.append(mapping[sid])
            if sid == "both":
                parse.usage_contexts.append("gaming")
        parse.usage_contexts = list(dict.fromkeys(parse.usage_contexts))
        parse.confidence = max(parse.confidence, 0.88)
    elif field == "merchant" and selected and selected[0] != "undecided":
        from taksitlio.query_understanding.fast_parser import ResolvedEntityRef

        sid = selected[0]
        parse.merchant = ResolvedEntityRef(
            resolved_id=sid,
            display_name=labels.get(sid, sid),
            match_type="CLARIFICATION",
            confidence=0.95,
            required=True,
        )
        parse.confidence = max(parse.confidence, 0.9)

    if parse.positive_categories:
        parse.confidence = max(parse.confidence, 0.91)
        parse.requires_llm = False
    return parse
