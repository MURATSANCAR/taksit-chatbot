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
from taksitlio.semantic_matching.token_set_alias_retriever import (
    TokenSetAliasRetriever,
    TokenSetAliasScore,
)
from taksitlio.semantic_matching.turkish_normalize import (
    normalize_turkish,
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

        # ADR-008 P0: token-set alias retriever — surface / normalized /
        # token-set / prefix-safe / n-gram / morphological. Never uses
        # substring containment.
        token_set_retriever = TokenSetAliasRetriever(
            character_ngram_min_similarity=policy.character_ngram_min_similarity,
            character_ngram_min_token_length=policy.character_ngram_min_token_length,
            morphological_variant_min_length=policy.morphological_variant_min_length,
        )

        # Legacy normalization for use-case + lexical scorers.
        normalized_legacy = normalize_query(query.query_text, query.hint_texts)

        # ADR-008: build the query variant list once — the token-set
        # retriever scores each node against every surface / normalized /
        # concept / declared variant string. Morphological variants are
        # a separate list so they never fire direct_alias_match alone.
        positive_variants = query.positive_query_variants()
        morphological_query_variants = query.morphological_positive_variants()
        query_variants: tuple[str, ...] = _dedupe_case_insensitive(
            (query.query_text, *positive_variants)
        )
        negative_variants = query.negative_query_variants()
        correction_variants = query.correction_query_variants()

        # ---- Retrieve a candidate pool of size ≈ candidate_pool_size,
        # unioning alias / lexical / vector / use-case scans. Each retriever
        # emits its own top-N; we union + dedupe before ranking.
        raw_scored: list[
            tuple[
                CategorySnapshotNode,
                AliasScore,
                float,
                float,
                float,
                TokenSetAliasScore,
            ]
        ] = []
        for node in index.snapshot.nodes:
            alias_result = alias_matcher.score(normalized_legacy, node)
            lexical = lexical_scorer.score(normalized_legacy, node)
            use_case = use_case_scorer.score(normalized_legacy, node)
            vector = 0.0
            if not degraded and query_vector:
                record = loaded_embeddings.get(node.id)
                vector = vector_retriever.cosine(query_vector, record)
            token_score = token_set_retriever.score(
                query_variants,
                node,
                morphological_query_variants=morphological_query_variants,
            )
            raw_scored.append(
                (node, alias_result, lexical, use_case, vector, token_score)
            )

        # Build the pool: keep any node that scored above zero on any signal
        # (token-set channels included), then sort/trim to candidate_pool_size.
        pool_universe: list[
            tuple[
                CategorySnapshotNode,
                AliasScore,
                float,
                float,
                float,
                TokenSetAliasScore,
            ]
        ] = [
            entry
            for entry in raw_scored
            if entry[1].score > 0
            or entry[2] > 0
            or entry[3] > 0
            or entry[4] > 0
            or entry[5].aggregate_alias > 0
        ]
        pool_universe.sort(
            key=lambda e: (
                max(e[1].score, e[5].aggregate_alias),
                e[4],
                e[2],
                e[3],
            ),
            reverse=True,
        )
        pool_universe = pool_universe[: max(1, policy.candidate_pool_size)]
        retrieved_by: dict[str, str] = {}
        for node, alias_res, lexical, use_case, vector, token_score in pool_universe:
            retrieved_by[node.id] = _classify_retrieval_channel(
                alias_res, lexical, use_case, vector, token_score
            )

        candidates: list[CategoryCandidate] = []
        for (
            node,
            alias_result,
            lexical,
            use_case,
            vector,
            token_score,
        ) in pool_universe:
            hierarchy = _hierarchy_boost(node, index, alias_result)

            # ADR-008: merge the token-set aggregate into the legacy alias
            # score so the hybrid scorer sees the stronger surface / token
            # signal. Legacy alias EXACT mode still contributes for
            # display-name fallback.
            alias_aggregate = max(alias_result.score, token_score.aggregate_alias)

            # ADR-008: direct_alias_match is set ONLY when the surface or
            # normalized exact channel fired above threshold. Token-set /
            # prefix-safe / n-gram / morphological alone must NOT drive
            # DIRECT_ALIAS_AUTO_SELECT.
            direct_alias_match, alias_boost = _adr008_direct_alias_bonus(
                token_score, alias_result, policy
            )

            # Negative penalties.
            neg_alias_hit = token_set_retriever.matches_negative_hard_exclude(
                negative_variants, node
            )
            neg_correction_hit = token_set_retriever.matches_negative_hard_exclude(
                correction_variants, node
            )
            # ADR-008: if a positive surface also hits the same node
            # (sibling aliases like kulaklık/hoparlör on audio), do NOT
            # hard-exclude — soft-penalise and refuse direct-alias auto-select.
            pos_alias_hit = False
            if negative_variants or correction_variants:
                pos_alias_hit = token_set_retriever.matches_negative_hard_exclude(
                    positive_variants or (query.query_text,), node
                )
            conflict_same_node = bool(
                (neg_alias_hit or neg_correction_hit) and pos_alias_hit
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

            # Hard-exclude — never surface these to the ranker, unless the
            # positive channel also hits the same node (sibling-alias conflict).
            if (
                neg_alias_hit
                and policy.hard_exclude_exact_negative_alias
                and not conflict_same_node
            ):
                continue
            if (
                neg_correction_hit
                and policy.hard_exclude_user_correction
                and not conflict_same_node
            ):
                continue

            if conflict_same_node:
                # Sibling aliases share a category (kulaklık/hoparlör → audio).
                # Keep the candidate. Allow DIRECT_ALIAS only when the
                # *positive* channel still has a surface/normalized exact hit
                # on this node — otherwise refuse auto-select.
                pos_surface = token_set_retriever.score(
                    positive_variants or (query.query_text,), node
                )
                if not (
                    pos_surface.surface_exact >= 0.9
                    or pos_surface.normalized_exact >= 0.9
                ):
                    direct_alias_match = False
                    alias_boost = 0.0
                explicit_negation_penalty = max(
                    explicit_negation_penalty,
                    policy.explicit_negative_penalty * 0.35,
                )
            breakdown = SignalBreakdown(
                alias=alias_aggregate,
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
                surface_exact_alias=token_score.surface_exact,
                normalized_exact_alias=token_score.normalized_exact,
                token_set_alias=token_score.token_set,
                prefix_safe_alias=token_score.prefix_safe,
                character_ngram=token_score.character_ngram,
                morphological_variant=token_score.morphological_variant,
                negative_penalty=max(
                    explicit_negation_penalty, correction_penalty_val
                ),
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
        # ADR-008 diagnostics: surface / normalized / variants + retrieval
        # channels. Raw utterance is NEVER placed here.
        surface_concepts, normalized_concepts, variant_values = _summarise_constraints(
            query
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
                "surface_concepts": list(surface_concepts),
                "normalized_concepts": list(normalized_concepts),
                "variants": list(variant_values),
                "positive_query_variants": list(query.positive_query_variants()),
                "morphological_query_variants": list(
                    query.morphological_positive_variants()
                ),
                "negative_query_variants": list(negative_variants),
                "correction_query_variants": list(correction_variants),
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


def _adr008_direct_alias_bonus(
    token_score: TokenSetAliasScore,
    alias_result: AliasScore,
    policy: SemanticMatchPolicy,
) -> tuple[bool, float]:
    """Return (is_direct_alias, additive_score_bonus) — ADR-008.

    Only surface / normalized exact channels may set ``direct_alias_match``.
    Token-set / prefix-safe / character n-gram / morphological signals
    contribute to ranking via the alias aggregate, but they can NEVER
    fire the DIRECT_ALIAS_AUTO_SELECT decision path.

    Legacy AliasMatcher EXACT hits with weight ≥ direct_alias_minimum_weight
    still count as a direct alias — the legacy channel remains for
    display-name fallback in seed catalogs.
    """

    # ADR-008 primary path: surface / normalized exact via token-set.
    if token_score.surface_exact >= policy.direct_alias_minimum_weight:
        if policy.surface_exact_can_auto_select:
            return True, policy.exact_alias_boost
    if token_score.normalized_exact >= policy.direct_alias_minimum_weight:
        if policy.surface_exact_can_auto_select:
            return True, policy.exact_alias_boost

    # Legacy fallback: EXACT alias mode with high weight (unchanged from
    # ADR-006 behaviour). Keeps display-name driven auto-select working.
    if alias_result.mode is not None and alias_result.score > 0:
        mode = alias_result.mode.value.upper() if alias_result.mode else ""
        if mode == "EXACT" and alias_result.score >= policy.direct_alias_minimum_weight:
            return True, policy.exact_alias_boost

    # Token-set / prefix-safe / n-gram / morphological — apply the softer
    # direct_alias_boost so ranking still rewards them, but do NOT flip
    # direct_alias_match.
    if token_score.aggregate_alias >= 0.9:
        return False, policy.direct_alias_boost
    return False, 0.0


def _classify_retrieval_channel(
    alias_res: AliasScore,
    lexical: float,
    use_case: float,
    vector: float,
    token_score: TokenSetAliasScore,
) -> str:
    """Assign a single retrieval channel label per node for diagnostics."""

    if token_score.surface_exact > 0:
        return "SURFACE_EXACT_ALIAS"
    if token_score.normalized_exact > 0:
        return "NORMALIZED_EXACT_ALIAS"
    if token_score.token_set > 0:
        return "TOKEN_SET_ALIAS"
    if token_score.prefix_safe > 0:
        return "PREFIX_SAFE_ALIAS"
    if token_score.morphological_variant > 0:
        return "MORPHOLOGICAL_VARIANT"
    if token_score.character_ngram > 0:
        return "CHARACTER_NGRAM"
    if alias_res.score > 0:
        return "alias"
    if vector > 0 and vector >= max(lexical, use_case):
        return "vector"
    if lexical > 0:
        return "lexical"
    if use_case > 0:
        return "use_case"
    return "unknown"


def _dedupe_case_insensitive(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        key = v.casefold().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return tuple(out)


def _summarise_constraints(
    query: MatchQuery,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (surface_concepts, normalized_concepts, variant_values).

    Walks the semantic_constraints dict directly (surface / normalized /
    variant values). Never surfaces the raw utterance.
    """

    surface: list[str] = []
    normalized: list[str] = []
    variants: list[str] = []
    if query.semantic_constraints:
        for slot in ("positive", "negative", "corrections"):
            entries = query.semantic_constraints.get(slot) or ()
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                s = entry.get("surface_form")
                if isinstance(s, str) and s.strip():
                    surface.append(s.strip())
                n = entry.get("normalized_form")
                if isinstance(n, str) and n.strip():
                    normalized.append(n.strip())
                vs = entry.get("variants") or ()
                if isinstance(vs, (list, tuple)):
                    for v in vs:
                        if isinstance(v, Mapping):
                            val = v.get("value")
                            if isinstance(val, str) and val.strip():
                                variants.append(val.strip())
    return (
        _dedupe_case_insensitive(surface),
        _dedupe_case_insensitive(normalized),
        _dedupe_case_insensitive(variants),
    )


def _query_hash(text: str) -> str:
    normalized = " ".join((text or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "SemanticCategoryMatcher",
    "SemanticMatchPolicyProvider",
    "StaticSemanticMatchPolicyProvider",
]
