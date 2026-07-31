"""Source conflict gate + precedence policy (ADR-012 §6–7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional, Sequence

from taksitlio.answer_integrity.errors import SourceConflictUnresolved
from taksitlio.answer_integrity.truth_status import FieldTruthStatus


class DataKind(str, Enum):
    PRODUCT_ATTRIBUTE = "PRODUCT_ATTRIBUTE"
    PRICE = "PRICE"
    STOCK = "STOCK"
    BANK_CAMPAIGN = "BANK_CAMPAIGN"
    MERCHANT_BANK_AGREEMENT = "MERCHANT_BANK_AGREEMENT"
    MONTHLY_PAYMENT = "MONTHLY_PAYMENT"
    PRODUCT_IMAGE = "PRODUCT_IMAGE"


# Default precedence when DB policy not loaded — ordered highest→lowest.
DEFAULT_PRECEDENCE: Mapping[str, tuple[str, ...]] = {
    DataKind.PRODUCT_ATTRIBUTE.value: (
        "manufacturer",
        "merchant_feed",
        "merchant_page",
        "enrichment",
    ),
    DataKind.PRICE.value: ("merchant_api", "merchant_feed", "merchant_page"),
    DataKind.STOCK.value: ("merchant_api", "merchant_feed", "merchant_page"),
    DataKind.BANK_CAMPAIGN.value: (
        "bank_api",
        "bank_official",
        "merchant_agreement",
    ),
    DataKind.MERCHANT_BANK_AGREEMENT.value: (
        "taksitlio_verified",
        "merchant_source",
        "bank_source",
    ),
    DataKind.MONTHLY_PAYMENT.value: ("source_provided_plan", "deterministic_calc"),
    DataKind.PRODUCT_IMAGE.value: ("merchant_verified", "manufacturer_verified"),
}


@dataclass(frozen=True)
class SourceObservation:
    source_class: str
    value: str
    observed_at: Optional[datetime | str] = None
    specificity: int = 0  # higher = more product/merchant/category specific
    agreement_scope_ok: bool = True

    def __post_init__(self) -> None:
        raw = self.observed_at
        if isinstance(raw, str):
            text = raw.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = datetime.strptime(text[:10], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            object.__setattr__(self, "observed_at", parsed)


@dataclass(frozen=True)
class ConflictResolution:
    status: FieldTruthStatus
    chosen: Optional[SourceObservation]
    reason: str
    candidates: tuple[SourceObservation, ...] = ()


@dataclass(frozen=True)
class SourcePrecedencePolicy:
    """DB-managed precedence; never hardcode merchant/bank names."""

    policy_code: str = "DEFAULT"
    version: int = 1
    precedence_by_kind: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_PRECEDENCE)
    )

    def order_for(self, kind: DataKind | str) -> tuple[str, ...]:
        key = kind.value if isinstance(kind, DataKind) else kind
        return tuple(self.precedence_by_kind.get(key) or ())


def _rank(
    obs: SourceObservation,
    order: Sequence[str],
) -> tuple[int, float, int, int]:
    try:
        prec = order.index(obs.source_class)
    except ValueError:
        prec = len(order) + 10
    at = obs.observed_at
    if isinstance(at, datetime):
        ts = at.timestamp()
    else:
        ts = float("-inf")
    scope = 1 if obs.agreement_scope_ok else 0
    return (prec, -ts, -obs.specificity, -scope)


def resolve_conflict(
    kind: DataKind | str,
    observations: Sequence[SourceObservation],
    *,
    policy: SourcePrecedencePolicy | None = None,
    raise_if_unresolved: bool = False,
) -> ConflictResolution:
    """Resolve or mark CONFLICTED. Never silently pick without policy."""

    pol = policy or SourcePrecedencePolicy()
    values = {o.value for o in observations}
    if len(observations) <= 1 or len(values) <= 1:
        chosen = observations[0] if observations else None
        return ConflictResolution(
            status=FieldTruthStatus.VERIFIED if chosen else FieldTruthStatus.UNAVAILABLE,
            chosen=chosen,
            reason="single_or_agreeing",
            candidates=tuple(observations),
        )

    order = pol.order_for(kind)
    ranked = sorted(observations, key=lambda o: _rank(o, order))
    best = ranked[0]
    # If top two share same precedence rank and disagree → unresolved.
    if len(ranked) >= 2:
        r0 = _rank(best, order)[0]
        r1 = _rank(ranked[1], order)[0]
        if r0 == r1 and best.value != ranked[1].value:
            if raise_if_unresolved:
                raise SourceConflictUnresolved(
                    kind.value if isinstance(kind, DataKind) else str(kind),
                    f"{best.value} vs {ranked[1].value}",
                )
            return ConflictResolution(
                status=FieldTruthStatus.CONFLICTED,
                chosen=None,
                reason="equal_precedence_disagreement",
                candidates=tuple(ranked),
            )
    return ConflictResolution(
        status=FieldTruthStatus.VERIFIED,
        chosen=best,
        reason="precedence_resolved",
        candidates=tuple(ranked),
    )


def conflict_user_message(
    field_label: str,
    shown_value: Optional[str],
    *,
    pending_value: Optional[str] = None,
) -> str:
    base = (
        f"Bu ürün için {field_label} bilgileri kaynaklar arasında farklılık gösteriyor."
    )
    if shown_value:
        base += f" Doğrulanmış {shown_value} seçeneğini gösterebilirim"
        if pending_value:
            base += f"; {pending_value} seçeneği yeniden kontrol ediliyor"
        base += "."
    else:
        base += " Doğrulanmış bir değer seçilemedi."
    return base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "DEFAULT_PRECEDENCE",
    "ConflictResolution",
    "DataKind",
    "SourceObservation",
    "SourcePrecedencePolicy",
    "conflict_user_message",
    "resolve_conflict",
    "utcnow",
]
