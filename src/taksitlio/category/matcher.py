"""Semantic category matching against dynamic catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from taksitlio.embeddings.client import Embedder
from taksitlio.embeddings.vectors import bag_of_chars_embedding, cosine_similarity


@dataclass(frozen=True)
class Category:
    id: int
    category_code: str
    display_name: str
    description: str
    synonyms: tuple[str, ...] = ()
    status: str = "ACTIVE"
    embedding: tuple[float, ...] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def match_text(self) -> str:
        parts = [self.display_name, self.description, *self.synonyms]
        return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class CategoryMatchPolicy:
    policy_code: str
    minimum_score: float = 0.55
    maximum_candidates: int = 3
    clarify_score_gap: float = 0.08


@dataclass(frozen=True)
class CategoryMatch:
    category: Category
    score: float


@dataclass(frozen=True)
class CategoryMatchResult:
    matches: list[CategoryMatch]
    needs_clarification: bool
    score_gap: float | None
    query_embedding: list[float] = field(default_factory=list)


class CategoryRepository(Protocol):
    async def list_active(self) -> list[Category]: ...

    async def get_match_policy(self, policy_code: str = "DEFAULT") -> CategoryMatchPolicy: ...


class InMemoryCategoryRepository:
    def __init__(
        self,
        categories: Sequence[Category],
        policy: CategoryMatchPolicy | None = None,
    ) -> None:
        self._categories = list(categories)
        self._policy = policy or CategoryMatchPolicy(policy_code="DEFAULT")

    async def list_active(self) -> list[Category]:
        return [c for c in self._categories if c.status == "ACTIVE"]

    async def get_match_policy(self, policy_code: str = "DEFAULT") -> CategoryMatchPolicy:
        return self._policy


class SemanticCategoryMatcher:
    """
    Matches need_description to categories via embeddings.

    Does not depend on hardcoded category codes in application logic —
    catalog comes from the repository (DB).
    """

    def __init__(
        self,
        repository: CategoryRepository,
        embedder: Embedder,
    ) -> None:
        self._repo = repository
        self._embedder = embedder

    async def match(
        self,
        need_description: str,
        *,
        policy_code: str = "DEFAULT",
        extra_texts: Sequence[str] | None = None,
    ) -> CategoryMatchResult:
        policy = await self._repo.get_match_policy(policy_code)
        categories = await self._repo.list_active()
        if not categories:
            return CategoryMatchResult(matches=[], needs_clarification=False, score_gap=None)

        query_parts = [need_description]
        if extra_texts:
            query_parts.extend(extra_texts)
        query_text = " ".join(query_parts)

        query_vecs = await self._embedder.embed([query_text])
        query_vec = query_vecs[0]

        scored: list[CategoryMatch] = []
        for category in categories:
            cat_vec = list(category.embedding) if category.embedding else None
            if cat_vec is None:
                embedded = await self._embedder.embed([category.match_text])
                cat_vec = embedded[0]
            score = cosine_similarity(query_vec, cat_vec)
            # lexical boost for exact synonym hits
            score = max(score, _synonym_boost(query_text, category))
            if score >= policy.minimum_score:
                scored.append(CategoryMatch(category=category, score=score))

        scored.sort(key=lambda m: m.score, reverse=True)
        top = scored[: policy.maximum_candidates]

        score_gap: float | None = None
        needs_clarification = False
        if len(top) >= 2:
            score_gap = top[0].score - top[1].score
            if score_gap <= policy.clarify_score_gap:
                needs_clarification = True
        elif len(top) == 0:
            needs_clarification = True

        return CategoryMatchResult(
            matches=top,
            needs_clarification=needs_clarification,
            score_gap=score_gap,
            query_embedding=query_vec,
        )


def _synonym_boost(query: str, category: Category) -> float:
    q = query.casefold()
    best = 0.0
    for syn in category.synonyms:
        s = syn.casefold()
        if s and s in q:
            best = max(best, 0.82)
        elif s and any(tok == s for tok in q.replace(",", " ").split()):
            best = max(best, 0.88)
    name = category.display_name.casefold()
    if name and name in q:
        best = max(best, 0.85)
    return best


def bootstrap_category_with_lexical_embedding(category: Category, *, dim: int = 256) -> Category:
    emb = bag_of_chars_embedding(category.match_text, dim=dim)
    return Category(
        id=category.id,
        category_code=category.category_code,
        display_name=category.display_name,
        description=category.description,
        synonyms=category.synonyms,
        status=category.status,
        embedding=tuple(emb),
        metadata=category.metadata,
    )
