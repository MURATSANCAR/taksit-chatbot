"""Semantic category matcher package."""

from taksitlio.semantic_matching.cache import (
    CategoryMatchCache,
    InMemoryCategoryMatchCache,
    NoOpCategoryMatchCache,
    build_cache_key,
)
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
from taksitlio.semantic_matching.embedding_gateway import (
    AlwaysFailingGateway,
    EmbedderQueryGateway,
    LexicalFallbackGateway,
    QueryEmbeddingGateway,
)
from taksitlio.semantic_matching.errors import (
    CatalogUnavailable,
    EmbeddingGatewayUnavailable,
    SemanticMatchingError,
)
from taksitlio.semantic_matching.hybrid_scorer import HybridScorer
from taksitlio.semantic_matching.matcher import (
    SemanticCategoryMatcher,
    SemanticMatchPolicyProvider,
    StaticSemanticMatchPolicyProvider,
)
from taksitlio.semantic_matching.observability import (
    InMemoryMatcherMetricsHook,
    MatcherMetricsHook,
    NoOpMatcherMetricsHook,
)
from taksitlio.semantic_matching.state_bridge import (
    CategoryResolutionApplier,
    CategoryResolutionApplyOutcome,
)

__all__ = [
    "AlwaysFailingGateway",
    "CatalogUnavailable",
    "CategoryCandidate",
    "CategoryMatchCache",
    "CategoryMatchDecision",
    "CategoryMatchResult",
    "CategoryMatchStatus",
    "CategoryResolutionApplier",
    "CategoryResolutionApplyOutcome",
    "DecisionPolicy",
    "EmbedderQueryGateway",
    "EmbeddingGatewayUnavailable",
    "HybridScorer",
    "InMemoryCategoryMatchCache",
    "InMemoryMatcherMetricsHook",
    "LexicalFallbackGateway",
    "MatchQuery",
    "MatcherMetricsHook",
    "NoOpCategoryMatchCache",
    "NoOpMatcherMetricsHook",
    "QueryEmbeddingGateway",
    "SemanticCategoryMatcher",
    "SemanticMatchPolicy",
    "SemanticMatchPolicyProvider",
    "SemanticMatchingError",
    "SignalBreakdown",
    "StaticSemanticMatchPolicyProvider",
    "build_cache_key",
]
