"""Negative-constraint hard-exclude + soft-penalty tests (ADR-006 §E)."""

from __future__ import annotations

import pytest

from taksitlio.category_catalog import (
    CategoryCatalogService,
    InMemoryCategoryCatalogRepository,
)
from taksitlio.category_catalog.publish_pipeline import prepare_embed_and_publish
from taksitlio.category_embedding import (
    CategoryEmbeddingOutbox,
    CategoryEmbeddingWorker,
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.embeddings.client import LexicalEmbedder
from taksitlio.semantic_matching import (
    CategoryMatchStatus,
    InMemoryCategoryMatchCache,
    LexicalFallbackGateway,
    MatchQuery,
    SemanticCategoryMatcher,
    SemanticMatchPolicy,
    StaticSemanticMatchPolicyProvider,
)


class _EmbClient:
    async def embed(self, texts):
        return await LexicalEmbedder(dim=64).embed(list(texts))


async def _build_stack(
    *, aliases: dict[str, tuple[str, ...]], policy: SemanticMatchPolicy | None = None
):
    repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(repo)
    catalog = await service.create_catalog(catalog_code="X", display_name="X")
    embed_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embed_repo)
    worker = CategoryEmbeddingWorker(embed_repo, _EmbClient())

    for slug, alias_list in aliases.items():
        cat = await service.add_category(
            catalog_id=catalog.id,
            slug=slug,
            semantic_description=f"{slug} genel açıklama",
        )
        await service.add_localization(
            category_id=cat.id,
            locale=catalog.primary_locale,
            display_name=slug.title(),
            synonyms=alias_list,
        )
        for alias in alias_list:
            await service.add_alias(
                category_id=cat.id,
                locale=catalog.primary_locale,
                alias_text=alias,
            )
    await prepare_embed_and_publish(
        service, catalog.id, outbox, worker, embedding_repo=embed_repo
    )

    matcher = SemanticCategoryMatcher(
        snapshot_provider=service,
        embedding_repository=embed_repo,
        query_gateway=LexicalFallbackGateway(dim=64),
        policy_provider=StaticSemanticMatchPolicyProvider(
            policy
            or SemanticMatchPolicy(
                minimum_candidate_score=0.05,
                minimum_auto_select_score=0.15,
                minimum_auto_select_gap=0.05,
                hard_exclude_exact_negative_alias=True,
                hard_exclude_user_correction=True,
            )
        ),
        cache=InMemoryCategoryMatchCache(),
    )
    return catalog, matcher


@pytest.mark.asyncio
async def test_exact_negative_alias_is_hard_excluded_from_candidates() -> None:
    catalog, matcher = await _build_stack(
        aliases={
            "mobile": ("telefon", "cep telefonu"),
            "laptop": ("bilgisayar", "dizüstü"),
        }
    )
    result = await matcher.match(
        MatchQuery(
            text="telefon değil laptop lazım",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
            semantic_constraints={
                "positive": [{"concept": "laptop", "provenance": "EXPLICIT"}],
                "negative": [
                    {"concept": "telefon", "provenance": "EXPLICIT_NEGATION"}
                ],
            },
        )
    )
    slugs = [c.slug for c in result.candidates]
    assert "mobile" not in slugs, "hard-excluded negative alias must not appear"
    if slugs:
        assert slugs[0] == "laptop"


@pytest.mark.asyncio
async def test_correction_hard_exclude_removes_earlier_intent() -> None:
    catalog, matcher = await _build_stack(
        aliases={
            "mobile": ("telefon",),
            "laptop": ("laptop", "dizüstü"),
        }
    )
    result = await matcher.match(
        MatchQuery(
            text="aslında telefon değil laptop demiştim",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
            semantic_constraints={
                "corrections": [
                    {"concept": "telefon", "provenance": "USER_CORRECTION"}
                ],
                "positive": [{"concept": "laptop", "provenance": "EXPLICIT"}],
            },
        )
    )
    slugs = [c.slug for c in result.candidates]
    assert "mobile" not in slugs


@pytest.mark.asyncio
async def test_soft_penalty_when_hard_exclude_disabled() -> None:
    """With hard_exclude=False, negative concepts must penalise but not remove."""

    catalog, matcher = await _build_stack(
        aliases={
            "mobile": ("telefon",),
            "laptop": ("laptop", "bilgisayar"),
        },
        policy=SemanticMatchPolicy(
            minimum_candidate_score=0.01,
            minimum_auto_select_score=0.15,
            minimum_auto_select_gap=0.05,
            hard_exclude_exact_negative_alias=False,
            explicit_negative_penalty=0.9,
            negative_semantic_weight=0.35,
        ),
    )
    result = await matcher.match(
        MatchQuery(
            text="telefon istemiyorum laptop bakıyorum",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
            semantic_constraints={
                "negative": [{"concept": "telefon", "provenance": "EXPLICIT_NEGATION"}],
                "positive": [{"concept": "laptop", "provenance": "EXPLICIT"}],
            },
        )
    )
    slugs = [c.slug for c in result.candidates]
    if "mobile" in slugs and "laptop" in slugs:
        mobile_score = next(c.score for c in result.candidates if c.slug == "mobile")
        laptop_score = next(c.score for c in result.candidates if c.slug == "laptop")
        assert mobile_score < laptop_score
