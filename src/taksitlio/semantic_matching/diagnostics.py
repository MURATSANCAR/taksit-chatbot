"""ADR-008 P0 — E2E retrieval diagnostics.

Provides a typed view over the ``CategoryMatchResult.diagnostics`` dict
so evaluators and dashboards can walk the pipeline stages:

    utterance → surface → normalized → variants → channels → pool
              → rank → decision

Reason codes:

    SURFACE_FORM_LOST
    OVER_NORMALIZED_CONCEPT
    ALIAS_VARIANT_MISSING
    TOKEN_SET_MISS
    CORRECT_CANDIDATE_RANKED_LOW

Nothing here stores the raw utterance — only surface / normalized /
variant concept strings that are already present in the validated
constraints and safe to log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    CategoryMatchResult,
)


class RetrievalReasonCode:
    SURFACE_FORM_LOST = "SURFACE_FORM_LOST"
    OVER_NORMALIZED_CONCEPT = "OVER_NORMALIZED_CONCEPT"
    ALIAS_VARIANT_MISSING = "ALIAS_VARIANT_MISSING"
    TOKEN_SET_MISS = "TOKEN_SET_MISS"
    CORRECT_CANDIDATE_RANKED_LOW = "CORRECT_CANDIDATE_RANKED_LOW"
    # ADR-008 P0.1 residual buckets.
    CORRECT_CANDIDATE_RANKED_3 = "CORRECT_CANDIDATE_RANKED_3"
    REQUIRED_SIBLING_MISSING = "REQUIRED_SIBLING_MISSING"
    NEGATIVE_PENALTY_TOO_STRONG = "NEGATIVE_PENALTY_TOO_STRONG"
    PARENT_CROWDS_OUT_CHILD = "PARENT_CROWDS_OUT_CHILD"


@dataclass(frozen=True)
class RetrievalDiagnostic:
    """Typed view of the ADR-008 P0 diagnostic fields."""

    surface_concepts: tuple[str, ...] = ()
    normalized_concepts: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    positive_query_variants: tuple[str, ...] = ()
    morphological_query_variants: tuple[str, ...] = ()
    negative_query_variants: tuple[str, ...] = ()
    correction_query_variants: tuple[str, ...] = ()
    retrieved_by: Mapping[str, str] = field(default_factory=dict)
    candidate_pool_ids: tuple[str, ...] = ()
    pool_size: int = 0
    considered: int = 0
    returned: int = 0
    decision_reason_code: Optional[str] = None
    reason_codes: tuple[str, ...] = ()
    top_signals: Mapping[str, Any] = field(default_factory=dict)
    diversity_notes: tuple[str, ...] = ()
    hierarchy_relations: tuple[Mapping[str, Any], ...] = ()
    concept_coverage: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "surface_concepts": list(self.surface_concepts),
            "normalized_concepts": list(self.normalized_concepts),
            "variants": list(self.variants),
            "positive_query_variants": list(self.positive_query_variants),
            "morphological_query_variants": list(self.morphological_query_variants),
            "negative_query_variants": list(self.negative_query_variants),
            "correction_query_variants": list(self.correction_query_variants),
            "retrieved_by": dict(self.retrieved_by),
            "candidate_pool_ids": list(self.candidate_pool_ids),
            "pool_size": self.pool_size,
            "considered": self.considered,
            "returned": self.returned,
            "decision_reason_code": self.decision_reason_code,
            "reason_codes": list(self.reason_codes),
            "top_signals": dict(self.top_signals),
            "diversity_notes": list(self.diversity_notes),
            "hierarchy_relations": [dict(r) for r in self.hierarchy_relations],
            "concept_coverage": dict(self.concept_coverage),
        }


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v is not None)
    return ()


def _top_signal_summary(top: Optional[CategoryCandidate]) -> dict:
    if top is None:
        return {}
    s = top.signals
    return {
        "category_id": top.category_id,
        "slug": top.slug,
        "score": top.score,
        "alias": s.alias,
        "surface_exact_alias": s.surface_exact_alias,
        "normalized_exact_alias": s.normalized_exact_alias,
        "token_set_alias": s.token_set_alias,
        "prefix_safe_alias": s.prefix_safe_alias,
        "character_ngram": s.character_ngram,
        "morphological_variant": s.morphological_variant,
        "direct_alias_match": s.direct_alias_match,
        "negative_penalty": s.negative_penalty,
    }


def _classify_reason_codes(
    top: Optional[CategoryCandidate],
    surface_concepts: Sequence[str],
    normalized_concepts: Sequence[str],
    variants: Sequence[str],
    positive_variants: Sequence[str],
) -> tuple[str, ...]:
    """Return provisional reason codes based on the retrieval outcome.

    * SURFACE_FORM_LOST — matcher had positive constraints but no surface
      form was propagated to the query variants (previous FAST outputs
      that stripped the surface).
    * OVER_NORMALIZED_CONCEPT — surface differs from normalized AND the
      top candidate only hit via character n-gram / morphological
      variant channels.
    * ALIAS_VARIANT_MISSING — top exists but token_set / prefix_safe /
      surface / normalized channels are all zero (only n-gram fired).
    * TOKEN_SET_MISS — variants were declared but no node hit any
      token-set / surface channel at all.
    """

    codes: list[str] = []
    has_surface_positive = any(surface_concepts)
    has_variants_declared = any(variants)

    if positive_variants and not has_surface_positive:
        codes.append(RetrievalReasonCode.SURFACE_FORM_LOST)

    if top is None:
        if positive_variants:
            codes.append(RetrievalReasonCode.TOKEN_SET_MISS)
        return tuple(codes)

    s = top.signals
    surface_or_token = (
        s.surface_exact_alias > 0
        or s.normalized_exact_alias > 0
        or s.token_set_alias > 0
        or s.prefix_safe_alias > 0
    )
    only_soft = (
        not surface_or_token
        and (s.character_ngram > 0 or s.morphological_variant > 0)
    )
    surface_differs = any(
        sf.casefold().strip() not in {n.casefold().strip() for n in normalized_concepts}
        for sf in surface_concepts
    )
    if only_soft and surface_differs:
        codes.append(RetrievalReasonCode.OVER_NORMALIZED_CONCEPT)
    if only_soft:
        codes.append(RetrievalReasonCode.ALIAS_VARIANT_MISSING)
    if has_variants_declared and not surface_or_token and s.character_ngram == 0:
        codes.append(RetrievalReasonCode.TOKEN_SET_MISS)
    return tuple(dict.fromkeys(codes))


def build_from_match_result(
    result: CategoryMatchResult,
    *,
    expected_category_id: Optional[str] = None,
) -> RetrievalDiagnostic:
    """Build a RetrievalDiagnostic from a CategoryMatchResult.

    ``expected_category_id`` is optional — when supplied and the
    expected category is present in the ranked candidates below rank 1,
    a ``CORRECT_CANDIDATE_RANKED_LOW`` reason code is added.
    """

    diag = result.diagnostics or {}
    surface = _as_tuple(diag.get("surface_concepts"))
    normalized = _as_tuple(diag.get("normalized_concepts"))
    variants = _as_tuple(diag.get("variants"))
    positive_variants = _as_tuple(diag.get("positive_query_variants"))
    morph_variants = _as_tuple(diag.get("morphological_query_variants"))
    negative_variants = _as_tuple(diag.get("negative_query_variants"))
    correction_variants = _as_tuple(diag.get("correction_query_variants"))
    retrieved_by_raw = diag.get("retrieved_by") or {}
    retrieved_by = (
        {str(k): str(v) for k, v in retrieved_by_raw.items()}
        if isinstance(retrieved_by_raw, Mapping)
        else {}
    )
    pool_ids = _as_tuple(diag.get("candidate_pool_ids"))
    pool_size = int(diag.get("pool_size") or 0)
    considered = int(diag.get("considered") or 0)
    returned = int(diag.get("returned") or 0)
    decision_reason_code = (
        str(diag.get("decision_reason_code"))
        if diag.get("decision_reason_code")
        else (result.decision.reason_code if result.decision else None)
    )

    top = result.candidates[0] if result.candidates else None
    reason_codes = list(
        _classify_reason_codes(
            top,
            surface_concepts=surface,
            normalized_concepts=normalized,
            variants=variants,
            positive_variants=positive_variants,
        )
    )
    if expected_category_id is not None and result.candidates:
        ranks = {c.category_id: c.rank for c in result.candidates}
        expected_rank = ranks.get(expected_category_id)
        if expected_rank is not None and expected_rank > 1:
            reason_codes.append(RetrievalReasonCode.CORRECT_CANDIDATE_RANKED_LOW)
            if expected_rank >= 3:
                reason_codes.append(
                    RetrievalReasonCode.CORRECT_CANDIDATE_RANKED_3
                )
        elif expected_rank is None:
            reason_codes.append(
                RetrievalReasonCode.REQUIRED_SIBLING_MISSING
            )
        top_penalty = float(top.signals.negative_penalty) if top else 0.0
        if expected_rank is None and top_penalty <= 0 and negative_variants:
            reason_codes.append(
                RetrievalReasonCode.NEGATIVE_PENALTY_TOO_STRONG
            )
        # Parent crowds child: expected below rank 1 while a sibling of
        # the expected's parent occupies rank 1 in the candidate list.
        hierarchy_relations = diag.get("hierarchy_relations") or ()
        if expected_rank is not None and expected_rank > 1 and hierarchy_relations:
            top_rel = next(
                (r for r in hierarchy_relations if r.get("category_id") == top.category_id),
                None,
            )
            expected_rel = next(
                (r for r in hierarchy_relations if r.get("category_id") == expected_category_id),
                None,
            )
            if top_rel and expected_rel:
                top_ancestors = set(top_rel.get("ancestor_ids") or ())
                if expected_category_id in top_ancestors or top.category_id in (
                    expected_rel.get("ancestor_ids") or ()
                ):
                    reason_codes.append(
                        RetrievalReasonCode.PARENT_CROWDS_OUT_CHILD
                    )

    diversity_notes = _as_tuple(diag.get("diversity_notes"))
    hierarchy_relations_raw = diag.get("hierarchy_relations") or ()
    hierarchy_relations: tuple[Mapping[str, Any], ...] = tuple(
        dict(r) for r in hierarchy_relations_raw if isinstance(r, Mapping)
    )
    concept_coverage_raw = diag.get("concept_coverage") or {}
    concept_coverage = (
        {str(k): dict(v) if isinstance(v, Mapping) else v for k, v in concept_coverage_raw.items()}
        if isinstance(concept_coverage_raw, Mapping)
        else {}
    )

    return RetrievalDiagnostic(
        surface_concepts=surface,
        normalized_concepts=normalized,
        variants=variants,
        positive_query_variants=positive_variants,
        morphological_query_variants=morph_variants,
        negative_query_variants=negative_variants,
        correction_query_variants=correction_variants,
        retrieved_by=retrieved_by,
        candidate_pool_ids=pool_ids,
        pool_size=pool_size,
        considered=considered,
        returned=returned,
        decision_reason_code=decision_reason_code,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        top_signals=_top_signal_summary(top),
        diversity_notes=diversity_notes,
        hierarchy_relations=hierarchy_relations,
        concept_coverage=concept_coverage,
    )


__all__ = [
    "RetrievalDiagnostic",
    "RetrievalReasonCode",
    "build_from_match_result",
]
