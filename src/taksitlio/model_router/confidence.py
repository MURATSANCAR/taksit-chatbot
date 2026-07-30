"""System confidence — never trust model self-reported confidence alone."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class SemanticSignals:
    """Optional category-matcher signals. Neutral when matcher not yet wired.

    The three status fields (``semantic_match_status``, ``semantic_degraded``,
    ``catalog_consistent``) are populated by the dynamic category-catalog
    matcher (ADR-004). Older providers only supply score / gap; the neutral
    provider leaves the extras as None to preserve legacy confidence output.
    """

    semantic_match_score: float = 0.5
    semantic_score_gap: float = 1.0
    semantic_match_status: str | None = None
    semantic_degraded: bool = False
    catalog_consistent: bool = True


class SemanticSignalProvider(Protocol):
    def get_signals(
        self,
        need_profile: Mapping[str, Any] | None,
    ) -> SemanticSignals: ...


class NeutralSemanticSignalProvider:
    def get_signals(self, need_profile: Mapping[str, Any] | None) -> SemanticSignals:
        return SemanticSignals()


@dataclass(frozen=True)
class ConfidenceSignals:
    schema_valid: bool
    required_fields_consistent: bool
    budget_consistent: bool
    session_consistent: bool
    ambiguity_count: int
    model_reported_confidence: float
    semantic_match_score: float = 0.5
    semantic_score_gap: float = 1.0
    missing_information: bool = False
    multiple_independent_needs: bool = False
    budget_ambiguity: bool = False
    comprehension_failure: bool = False
    semantic_match_status: str | None = None
    semantic_degraded: bool = False
    catalog_consistent: bool = True


@dataclass(frozen=True)
class SystemConfidenceResult:
    model_reported_confidence: float
    system_confidence: float
    signals: ConfidenceSignals
    details: dict[str, Any] = field(default_factory=dict)


class SystemConfidenceEvaluator:
    """
    Deterministic confidence from structural validators + optional semantic signals.

    Model self-score is only one weak input.
    """

    def __init__(
        self,
        *,
        semantic: SemanticSignalProvider | None = None,
        model_weight: float = 0.15,
    ) -> None:
        self._semantic = semantic or NeutralSemanticSignalProvider()
        self._model_weight = model_weight

    def evaluate(
        self,
        payload: Mapping[str, Any] | None,
        *,
        schema_valid: bool,
        schema_errors: Sequence[str] | None = None,
        session_summary: Mapping[str, Any] | None = None,
    ) -> SystemConfidenceResult:
        model_conf = 0.0
        if isinstance(payload, Mapping):
            try:
                model_conf = float(payload.get("confidence") or 0.0)
            except (TypeError, ValueError):
                model_conf = 0.0
            model_conf = max(0.0, min(1.0, model_conf))

        if not schema_valid or payload is None:
            signals = ConfidenceSignals(
                schema_valid=False,
                required_fields_consistent=False,
                budget_consistent=False,
                session_consistent=True,
                ambiguity_count=0,
                model_reported_confidence=model_conf,
                comprehension_failure=True,
            )
            return SystemConfidenceResult(
                model_reported_confidence=model_conf,
                system_confidence=0.0,
                signals=signals,
                details={"schema_errors": list(schema_errors or [])},
            )

        budget_ok = _budget_consistent(payload.get("budget") or {})
        required_ok = _required_fields_consistent(payload)
        session_ok = _session_consistent(payload, session_summary)
        ambiguities = payload.get("ambiguities") or []
        ambiguity_count = len(ambiguities) if isinstance(ambiguities, list) else 0
        signals_obj = payload.get("signals") or {}
        clarification = payload.get("clarification") or {}

        missing_information = bool(clarification.get("required")) or any(
            isinstance(a, dict) and str(a.get("code") or "").startswith("MISSING_")
            for a in (ambiguities if isinstance(ambiguities, list) else [])
        )
        multiple_needs = bool(signals_obj.get("multiple_needs"))
        budget_ambiguity = bool(signals_obj.get("budget_payment_confusion")) or not budget_ok
        session_conflict = bool(signals_obj.get("conflicts_with_session")) or not session_ok

        semantic = self._semantic.get_signals(payload)

        structural = 0.0
        structural += 0.25 if schema_valid else 0.0
        structural += 0.20 if required_ok else 0.0
        structural += 0.20 if budget_ok else 0.0
        structural += 0.15 if session_ok and not session_conflict else 0.0
        structural += 0.10 if ambiguity_count == 0 else max(0.0, 0.10 - 0.03 * ambiguity_count)
        structural += 0.10 * max(0.0, min(1.0, semantic.semantic_match_score))

        # Close semantic candidates reduce confidence slightly (clarification path)
        if semantic.semantic_score_gap < 0.08:
            structural *= 0.9

        # ADR-004: soft penalty for ambiguous / no-match / degraded matcher output.
        status = (semantic.semantic_match_status or "").upper()
        if status in {"AMBIGUOUS", "NO_MATCH", "CATALOG_UNAVAILABLE"}:
            structural *= 0.85
        if semantic.semantic_degraded:
            structural *= 0.9
        if not semantic.catalog_consistent:
            structural *= 0.85

        system = (1.0 - self._model_weight) * structural + self._model_weight * model_conf
        system = max(0.0, min(1.0, system))

        # Hard caps: structural failures cannot CONTINUE on model bravado alone
        if not budget_ok or not required_ok:
            system = min(system, 0.55)
        if not session_ok:
            system = min(system, 0.60)

        signals = ConfidenceSignals(
            schema_valid=True,
            required_fields_consistent=required_ok,
            budget_consistent=budget_ok,
            session_consistent=session_ok and not session_conflict,
            ambiguity_count=ambiguity_count,
            model_reported_confidence=model_conf,
            semantic_match_score=semantic.semantic_match_score,
            semantic_score_gap=semantic.semantic_score_gap,
            missing_information=missing_information,
            multiple_independent_needs=multiple_needs,
            budget_ambiguity=budget_ambiguity,
            comprehension_failure=bool(signals_obj.get("indirect_or_complex")) and system < 0.78,
            semantic_match_status=semantic.semantic_match_status,
            semantic_degraded=semantic.semantic_degraded,
            catalog_consistent=semantic.catalog_consistent,
        )
        return SystemConfidenceResult(
            model_reported_confidence=model_conf,
            system_confidence=system,
            signals=signals,
            details={
                "structural": structural,
                "model_weight": self._model_weight,
            },
        )


def _required_fields_consistent(payload: Mapping[str, Any]) -> bool:
    need = payload.get("need_description")
    intent = payload.get("intent") or {}
    if not isinstance(need, str) or not need.strip():
        return False
    if not isinstance(intent, Mapping) or not intent.get("type"):
        return False
    return True


def _budget_consistent(budget: Mapping[str, Any]) -> bool:
    if not isinstance(budget, Mapping):
        return False
    btype = budget.get("type")
    value = budget.get("value")
    minimum = budget.get("minimum")
    maximum = budget.get("maximum")
    monthly = budget.get("monthly_payment")

    def _num(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    value_n, min_n, max_n, monthly_n = _num(value), _num(minimum), _num(maximum), _num(monthly)

    if min_n is not None and max_n is not None and min_n > max_n:
        return False
    if value_n is not None and max_n is not None and value_n > max_n * 1.0001:
        return False
    if value_n is not None and min_n is not None and value_n < min_n * 0.9999:
        return False

    if btype == "EXACT" and value_n is None:
        return False
    if btype == "APPROXIMATE" and value_n is None:
        return False
    if btype == "RANGE" and min_n is None and max_n is None:
        return False
    if btype == "MONTHLY_PAYMENT" and monthly_n is None and value_n is None:
        return False

    # Confusing: both total budget and monthly set as the same field usage
    if btype == "APPROXIMATE" and monthly_n is not None and value_n is not None:
        if monthly_n > value_n:
            return False
    return True


def _session_consistent(
    payload: Mapping[str, Any],
    session_summary: Mapping[str, Any] | None,
) -> bool:
    signals = payload.get("signals") or {}
    if signals.get("conflicts_with_session"):
        return False
    if not session_summary:
        return True
    previous = session_summary.get("need_profile") or {}
    if not previous:
        return True
    prev_intent = ((previous.get("intent") or {}).get("type"))
    new_intent = ((payload.get("intent") or {}).get("type"))
    if prev_intent and new_intent and prev_intent != new_intent:
        if new_intent not in {"CLARIFICATION_RESPONSE", "OTHER"}:
            # Not automatically a conflict; allow unless model flagged it
            return not bool(signals.get("conflicts_with_session"))
    return True
