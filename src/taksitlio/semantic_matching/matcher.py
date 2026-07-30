"""SemanticCategoryMatcher — dynamic, snapshot-driven, DB-free of category names.

Public contract:

    result = await matcher.match(MatchQuery(...))

The matcher never mutates conversation state or the catalog. It reads a
published snapshot, retrieves a candidate pool (positive query
projection + alias/lexical/vector retrievers), penalises or hard-
excludes negative constraints (ADR-006), applies parent-child collapse
and returns a typed decision. All thresholds and weights come from the
injected SemanticMatchPolicy.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from typing import Mapping, Optional, Protocol, Sequence

from taksitlio.category_catalog.domain import CategorySnapshot, CategorySnapshotNode
from taksitlio.category_embedding.in_memory_repository import (
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.semantic_matching.cache import (
    CategoryMatchCache,
    NoOpCategoryMatchCache,
    build_cache_key,
)
from taksitlio.semantic_matching.candidate_retriever import SnapshotProvider
from taksitlio.semantic_matching.decision_policy import DecisionPolicy
from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    CategoryMatchDecision,
    CategoryMatchResult,
    CategoryMatchStatus,
    MatchQuery,
    SemanticMatchPolicy,
    SignalBreakdown,
)
from taksitlio.semantic_matching.embedding_gateway import QueryEmbeddingGateway
from taksitlio.semantic_matching.errors import EmbeddingGatewayUnavailable
from taksitlio.semantic_matching.hierarchy_collapse import collapse_parent_child
from taksitlio.semantic_matching.hybrid_scorer import HybridScorer
from taksitlio.semantic_matching.in_memory_index import SnapshotIndex
from taksitlio.semantic_matching.lexical_retriever import (
    AliasMatcher,
    AliasScore,
    LexicalOverlapScorer,
    UseCaseScorer,
    normalize_query,
)
from taksitlio.semantic_matching.query_intent import classify_query_intent
from taksitlio.semantic_matching.observability import (
    MatcherMetricsHook,
    NoOpMatcherMetricsHook,
)
from taksitlio.semantic_matching.turkish_normalize import (
    normalize_turkish,
    trigram_similarity,
)
from taksitlio.semantic_matching.vector_retriever import VectorRetriever


class SemanticMatchPolicyProvider(Protocol):
    async def get(
        self, policy_code: str = "CATEGORY_MATCH_DEFAULT"
    ) -> SemanticMatchPolicy: ...


class StaticSemanticMatchPolicyProvider:
    def __init__(self, policy: SemanticMatchPolicy | None = None) -> None:
        self._policy = policy or SemanticMatchPolicy()

    async def get(
        self, policy_code: str = "CATEGORY_MATCH_DEFAULT"
    ) -> SemanticMatchPolicy:
        if (
            policy_code != self._policy.policy_code
            and policy_code != "CATEGORY_MATCH_DEFAULT"
        ):
            return self._policy
        return self._policy


class SemanticCategoryMatcher:
    def __init__(
        self,
        *,
        snapshot_provider: SnapshotProvider,
        embedding_repository: InMemoryCategoryEmbeddingRepository,
        query_gateway: QueryEmbeddingGateway,
        policy_provider: SemanticMatchPolicyProvider,
        cache: CategoryMatchCache | None = None,
        metrics: MatcherMetricsHook | None = None,
    ) -> None:
        self._snapshots = snapshot_provider
        self._embeddings = embedding_repository
        self._gateway = query_gateway
        self._policies = policy_provider
        self._cache = cache or NoOpCategoryMatchCache()
        self._metrics = metrics or NoOpMatcherMetricsHook()

    async def match(self, query: MatchQuery) -> CategoryMatchResult:
        started = time.perf_counter()
        policy = await self._policies.get()

        cache_key = build_cache_key(query, policy)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            self._metrics.incr("semantic_matcher_cache_hit_total")
            return replace(cached, cache_hit=True)

        snapshot = await self._snapshots.get_published_snapshot(
            query.catalog_id, locale=query.locale
        )
        if snapshot is None:
            return self._empty_result(
                query,
                policy,
                cache_key,
                status=CategoryMatchStatus.CATALOG_UNAVAILABLE,
                started=started,
                reason="no published snapshot for catalog",
            )
        if snapshot.is_empty:
            return self._empty_result(
                query,
                policy,
                cache_key,
                status=CategoryMatchStatus.CATALOG_EMPTY,
                started=started,
                reason="published snapshot has no active categories",
            )

        index = SnapshotIndex.build(snapshot)
        vector_retriever = VectorRetriever(self._embeddings)
        loaded_embeddings = await vector_retriever.load_embeddings(
            snapshot=snapshot, embedding_profile_id=query.embedding_profile_id
        )

        # ---- Positive projection: need_description + positive constraints
        # + preferences fold. Negative constraints are NOT embedded here —
        # they get their own projection so we can subtract them.
        positive_hints: tuple[str, ...] = tuple(
            list(query.hint_texts) + list(query.positive_concepts)
        )
        positive_text_for_norm = query.query_text
        positive_normalized_full = normalize_turkish(
            positive_text_for_norm, extra=positive_hints
        )
        negative_concepts = query.negative_concepts
        correction_concepts = query.corrections

        degraded_reasons: list[str] = []
        query_vector: list[float] = []
        negative_vector: list[float] = []
        if loaded_embeddings:
            try:
                query_vector = await self._gateway.embed_query(
                    positive_normalized_full.value
                )
            except EmbeddingGatewayUnavailable as exc:
                if not policy.allow_lexical_degraded_mode:
                    return self._empty_result(
                        query,
                        policy,
                        cache_key,
                        status=CategoryMatchStatus.CATALOG_UNAVAILABLE,
                        started=started,
                        reason=f"embedding gateway unavailable: {exc}",
                    )
                degraded_reasons.append(f"embedding_gateway_unavailable: {exc}")
            else:
                if negative_concepts:
                    try:
                        neg_text = " ".join(negative_concepts)
                        negative_vector = await self._gateway.embed_query(neg_text)
                    except EmbeddingGatewayUnavailable:
                        negative_vector = []
        else:
            if policy.allow_lexical_degraded_mode:
                degraded_reasons.append("no_ready_embeddings_for_revision")
            else:
                return self._empty_result(
                    query,
                    policy,
                    cache_key,
                    status=CategoryMatchStatus.CATALOG_UNAVAILABLE,
                    started=started,
                    reason="no embeddings ready and lexical degraded not allowed",
                )

        degraded = bool(degraded_reasons)

        scorer = HybridScorer(policy)
        alias_matcher = AliasMatcher(policy.fuzzy_min_similarity)
        lexical_scorer = LexicalOverlapScorer()
        use_case_scorer = UseCaseScorer()

        # Legacy normalization for use-case + lexical scorers.
        normalized_legacy = normalize_query(query.query_text, query.hint_texts)

        # ---- Retrieve a candidate pool of size ≈ candidate_pool_size,
        # unioning alias / lexical / vector / use-case scans. Each retriever
        # emits its own top-N; we union + dedupe before ranking.
        raw_scored: list[tuple[CategorySnapshotNode, AliasScore, float, float, float]] = []
        for node in index.snapshot.nodes:
            alias_result = alias_matcher.score(normalized_legacy, node)
            lexical = lexical_scorer.score(normalized_legacy, node)
            use_case = use_case_scorer.score(normalized_legacy, node)
            vector = 0.0
            if not degraded and query_vector:
                record = loaded_embeddings.get(node.id)
                vector = vector_retriever.cosine(query_vector, record)
            raw_scored.append((node, alias_result, lexical, use_case, vector))

        # Build the pool: keep any node that scored above zero on any signal,
        # then sort/trim to candidate_pool_size.
        pool_universe: list[
            tuple[CategorySnapshotNode, AliasScore, float, float, float]
        ] = [
            entry
            for entry in raw_scored
            if entry[1].score > 0
            or entry[2] > 0
            or entry[3] > 0
            or entry[4] > 0
        ]
        pool_universe.sort(
            key=lambda e: (e[1].score, e[4], e[2], e[3]),
            reverse=True,
        )
        pool_universe = pool_universe[: max(1, policy.candidate_pool_size)]
        retrieved_by: dict[str, str] = {}
        for node, alias_res, lexical, use_case, vector in pool_universe:
            channel: str
            if alias_res.score > 0:
                channel = "alias"
            elif vector > 0 and vector >= max(lexical, use_case):
                channel = "vector"
            elif lexical > 0:
                channel = "lexical"
            elif use_case > 0:
                channel = "use_case"
            else:
                channel = "unknown"
            retrieved_by[node.id] = channel

        candidates: list[CategoryCandidate] = []
        for node, alias_result, lexical, use_case, vector in pool_universe:
            hierarchy = _hierarchy_boost(node, index, alias_result)

            # Direct alias / exact alias policy boost.
            direct_alias_match, alias_boost = _direct_alias_bonus(
                alias_result, policy
            )

            # Negative penalties.
            neg_alias_hit = _has_exact_negative_alias(node, negative_concepts)
            neg_correction_hit = _has_exact_negative_alias(
                node, correction_concepts
            )
            negative_vector_score = 0.0
            if negative_vector and not degraded:
                record = loaded_embeddings.get(node.id)
                negative_vector_score = vector_retriever.cosine(
                    negative_vector, record
                )
            explicit_negation_penalty = 0.0
            if neg_alias_hit:
                explicit_negation_penalty = policy.explicit_negative_penalty
            elif (
                negative_vector_score > 0.0
                and negative_vector_score >= policy.negative_match_threshold
            ):
                explicit_negation_penalty = min(
                    1.0,
                    policy.negative_semantic_weight * negative_vector_score,
                )
            correction_penalty_val = 0.0
            if neg_correction_hit:
                correction_penalty_val = policy.correction_penalty

            # Hard-exclude — never surface these to the ranker.
            if (
                neg_alias_hit
                and policy.hard_exclude_exact_negative_alias
            ):
                continue
            if (
                neg_correction_hit
                and policy.hard_exclude_user_correction
            ):
                continue

            breakdown = SignalBreakdown(
                alias=alias_result.score,
                lexical=lexical,
                vector=vector,
                use_case=use_case,
                hierarchy=hierarchy,
                alias_mode=(
                    alias_result.mode.value if alias_result.mode else None
                ),
                alias_text=alias_result.text,
                positive_vector_score=vector,
                negative_vector_score=negative_vector_score,
                exact_negative_alias=neg_alias_hit,
                explicit_correction_penalty=correction_penalty_val,
                direct_alias_match=direct_alias_match,
                hierarchy_collapsed=False,
            )
            base_score = scorer.combine(breakdown, degraded=degraded)
            score = base_score + alias_boost
            score = max(0.0, min(1.0, score))
            # Apply negative penalties multiplicatively so a strong hit
            # from vector alone cannot overwhelm an explicit user "not".
            if explicit_negation_penalty > 0.0:
                score = max(0.0, score * (1.0 - explicit_negation_penalty))
            if correction_penalty_val > 0.0:
                score = max(0.0, score * (1.0 - correction_penalty_val))

            if score < policy.minimum_candidate_score and score <= 0.0:
                continue
            if score <= 0.0:
                continue
            candidates.append(
                CategoryCandidate(
                    category_id=node.id,
                    slug=node.slug,
                    display_name=node.display_name,
                    score=score,
                    rank=0,
                    signals=breakdown,
                    matchable=bool(getattr(node, "matchable", True)),
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)

        # Parent-child collapse before decision.
        collapse_result = collapse_parent_child(
            candidates, index=index, policy=policy
        )
        collapsed_candidates = list(collapse_result.candidates)

        limited = collapsed_candidates[: policy.maximum_candidates]
        ranked = tuple(
            CategoryCandidate(
                category_id=c.category_id,
                slug=c.slug,
                display_name=c.display_name,
                score=c.score,
                rank=rank + 1,
                signals=c.signals,
                matchable=c.matchable,
            )
            for rank, c in enumerate(limited)
        )

        decision = DecisionPolicy(policy).decide(
            ranked,
            degraded=degraded,
            collapsed_pairs=collapse_result.collapsed_pairs,
            multi_need_signal=query.multi_need_signal,
            intent_kind=classify_query_intent(query.query_text),
        )

        # Non-matchable (out-of-scope) nodes stay in pool diagnostics but never
        # appear in the final Top-K returned to callers / evaluation.
        final_candidates = tuple(
            CategoryCandidate(
                category_id=c.category_id,
                slug=c.slug,
                display_name=c.display_name,
                score=c.score,
                rank=rank + 1,
                signals=c.signals,
                matchable=c.matchable,
            )
            for rank, c in enumerate(c for c in ranked if c.matchable)
        )

        duration_ms = (time.perf_counter() - started) * 1000.0
        pool_snapshot_keys = tuple(
            e[0].id for e in pool_universe
        )
        result = CategoryMatchResult(
            query_text_hash=_query_hash(query.query_text),
            catalog_id=query.catalog_id,
            catalog_revision=snapshot.revision,
            locale=snapshot.locale,
            embedding_profile_id=query.embedding_profile_id,
            policy_code=policy.policy_code,
            candidates=final_candidates,
            decision=decision,
            duration_ms=duration_ms,
            degraded=degraded,
            degraded_reasons=tuple(degraded_reasons),
            cache_hit=False,
            diagnostics={
                "considered": len(candidates),
                "returned": len(final_candidates),
                "loaded_embeddings": len(loaded_embeddings),
                "pool_size": len(pool_universe),
                "candidate_pool_ids": list(pool_snapshot_keys),
                "non_matchable_excluded": [
                    c.category_id for c in ranked if not c.matchable
                ],
                "collapsed_pairs": [
                    {"kept": kept, "dropped": dropped}
                    for kept, dropped in collapse_result.collapsed_pairs
                ],
                "retrieved_by": dict(retrieved_by),
            },
        )

        await self._cache.put(cache_key, result, ttl_seconds=policy.cache_ttl_seconds)
        self._metrics.incr(
            "semantic_matcher_decision_total",
            status=decision.status.value,
            degraded="1" if degraded else "0",
        )
        self._metrics.observe("semantic_matcher_duration_ms", duration_ms)
        return result

    def _empty_result(
        self,
        query: MatchQuery,
        policy: SemanticMatchPolicy,
        cache_key: str,
        *,
        status: CategoryMatchStatus,
        started: float,
        reason: str,
    ) -> CategoryMatchResult:
        duration_ms = (time.perf_counter() - started) * 1000.0
        result = CategoryMatchResult(
            query_text_hash=_query_hash(query.query_text),
            catalog_id=query.catalog_id,
            catalog_revision=query.catalog_revision,
            locale=query.locale,
            embedding_profile_id=query.embedding_profile_id,
            policy_code=policy.policy_code,
            candidates=(),
            decision=CategoryMatchDecision(
                status=status,
                selected_category_id=None,
                score_gap=None,
                reason=reason,
                reason_code=status.value,
            ),
            duration_ms=duration_ms,
            degraded=False if status == CategoryMatchStatus.CATALOG_EMPTY else (
                status == CategoryMatchStatus.CATALOG_UNAVAILABLE
            ),
            degraded_reasons=(reason,)
            if status == CategoryMatchStatus.CATALOG_UNAVAILABLE
            else (),
            diagnostics={},
        )
        self._metrics.incr(
            "semantic_matcher_decision_total",
            status=status.value,
            degraded="1",
        )
        return result


def _hierarchy_boost(
    node: CategorySnapshotNode,
    index: SnapshotIndex,
    alias_result: AliasScore,
) -> float:
    if not node.ancestor_ids:
        return 0.0
    if alias_result.score <= 0.0:
        return 0.0
    return min(0.3, 0.05 * len(node.ancestor_ids))


def _direct_alias_bonus(
    alias_result: AliasScore,
    policy: SemanticMatchPolicy,
) -> tuple[bool, float]:
    """Return (is_direct_alias, additive_score_bonus).

    * EXACT alias with weight ≥ minimum → exact_alias_boost.
    * Any alias hit ≥ 0.9 → direct_alias_boost (softer bonus).
    """

    if not alias_result.mode or alias_result.score <= 0:
        return False, 0.0
    mode = alias_result.mode.value.upper() if alias_result.mode else ""
    if mode == "EXACT" and alias_result.score >= policy.direct_alias_minimum_weight:
        return True, policy.exact_alias_boost
    if alias_result.score >= 0.9:
        return True, policy.direct_alias_boost
    return False, 0.0


def _has_exact_negative_alias(
    node: CategorySnapshotNode,
    concepts: Sequence[str],
) -> bool:
    """True when any of the concepts trigger an exact/near-exact alias hit
    on ``node``. Uses ascii-fold + trigram similarity so users typing
    "telefon" trigger the hit even if the alias is stored as "cep telefonu"."""

    if not concepts:
        return False
    haystack: list[str] = []
    haystack.append(node.display_name)
    haystack.extend(node.synonyms)
    for alias in node.aliases:
        haystack.append(alias.alias_text)
    for concept in concepts:
        for text in haystack:
            if not text:
                continue
            if trigram_similarity(concept, text) >= 0.85:
                return True
            if concept.strip().casefold() in text.casefold():
                return True
    return False


def _query_hash(text: str) -> str:
    normalized = " ".join((text or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "SemanticCategoryMatcher",
    "SemanticMatchPolicyProvider",
    "StaticSemanticMatchPolicyProvider",
]
