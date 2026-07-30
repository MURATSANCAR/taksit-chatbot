"""SemanticCategoryMatcher — dynamic, snapshot-driven, DB-free of category names.

Public contract:

    result = await matcher.match(MatchQuery(...))

The matcher never mutates conversation state or the catalog. It reads a
published snapshot, computes a hybrid score per node, ranks candidates and
returns a typed decision. All thresholds and weights come from the injected
SemanticMatchPolicy.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from typing import Optional, Protocol

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
from taksitlio.semantic_matching.hybrid_scorer import HybridScorer
from taksitlio.semantic_matching.in_memory_index import SnapshotIndex
from taksitlio.semantic_matching.lexical_retriever import (
    AliasMatcher,
    LexicalOverlapScorer,
    UseCaseScorer,
    normalize_query,
)
from taksitlio.semantic_matching.observability import (
    MatcherMetricsHook,
    NoOpMatcherMetricsHook,
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

        degraded_reasons: list[str] = []
        query_vector: list[float] = []
        if loaded_embeddings:
            try:
                query_vector = await self._gateway.embed_query(
                    normalize_query(query.text, query.extra_hints)
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

        normalized = normalize_query(query.text, query.extra_hints)

        candidates: list[CategoryCandidate] = []
        for node in index.snapshot.nodes:
            alias_result = alias_matcher.score(normalized, node)
            lexical = lexical_scorer.score(normalized, node)
            use_case = use_case_scorer.score(normalized, node)
            vector = 0.0
            if not degraded and query_vector:
                record = loaded_embeddings.get(node.id)
                vector = vector_retriever.cosine(query_vector, record)
            hierarchy = _hierarchy_boost(node, index, alias_result)

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
            )
            score = scorer.combine(breakdown, degraded=degraded)
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
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        limited = candidates[: policy.maximum_candidates]
        ranked = tuple(
            CategoryCandidate(
                category_id=c.category_id,
                slug=c.slug,
                display_name=c.display_name,
                score=c.score,
                rank=rank + 1,
                signals=c.signals,
            )
            for rank, c in enumerate(limited)
        )

        decision = DecisionPolicy(policy).decide(ranked)

        duration_ms = (time.perf_counter() - started) * 1000.0
        result = CategoryMatchResult(
            query_text_hash=_query_hash(query.text),
            catalog_id=query.catalog_id,
            catalog_revision=snapshot.revision,
            locale=snapshot.locale,
            embedding_profile_id=query.embedding_profile_id,
            policy_code=policy.policy_code,
            candidates=ranked,
            decision=decision,
            duration_ms=duration_ms,
            degraded=degraded,
            degraded_reasons=tuple(degraded_reasons),
            cache_hit=False,
            diagnostics={
                "considered": len(candidates),
                "returned": len(ranked),
                "loaded_embeddings": len(loaded_embeddings),
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
            query_text_hash=_query_hash(query.text),
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
            ),
            duration_ms=duration_ms,
            degraded=(status != CategoryMatchStatus.MATCHED),
            degraded_reasons=(reason,) if reason else (),
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
    alias_result,
) -> float:
    if not node.ancestor_ids:
        return 0.0
    if alias_result.score <= 0.0:
        return 0.0
    # Ancestors slightly boost specificity: a child match should not be
    # blindly promoted over a matching root.
    return min(0.3, 0.05 * len(node.ancestor_ids))


def _query_hash(text: str) -> str:
    normalized = " ".join((text or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "SemanticCategoryMatcher",
    "SemanticMatchPolicyProvider",
    "StaticSemanticMatchPolicyProvider",
]
