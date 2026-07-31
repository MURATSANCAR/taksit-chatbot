"""Soft-exclusion residual tests (ADR-008 P0.1).

Cover the sibling / shared-category negative pattern where a strong
negative alias (``süpürge``/``saat``/``kulaklık``/``iphone``) exactly
matches a category while the positive (``robot``/``bileklik``/``kablolu``/
``android``) only appears as a whole token in a different, *unshared*
alias phrase of the same category. Before P0.1 this hard-excluded the
intended category.

The rescue is purely token-set + catalog-text based (no substring, no
category hardcodes). Safety must be preserved for out-of-scope cases:
an unrelated positive concept must NOT keep a negated category alive.
"""

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
    *,
    aliases: dict[str, tuple[str, ...]],
    semantic_descriptions: dict[str, str] | None = None,
    use_cases: dict[str, tuple[str, ...]] | None = None,
    policy: SemanticMatchPolicy | None = None,
):
    repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(repo)
    catalog = await service.create_catalog(catalog_code="X", display_name="X")
    embed_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embed_repo)
    worker = CategoryEmbeddingWorker(embed_repo, _EmbClient())

    semantic_descriptions = semantic_descriptions or {}
    use_cases = use_cases or {}

    for slug, alias_list in aliases.items():
        cat = await service.add_category(
            catalog_id=catalog.id,
            slug=slug,
            semantic_description=semantic_descriptions.get(slug, f"{slug} genel açıklama"),
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
        for use_case_text in use_cases.get(slug, ()):
            await service.add_use_case(
                category_id=cat.id,
                locale=catalog.primary_locale,
                use_case_text=use_case_text,
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
async def test_unshared_alias_rescues_shared_category_from_hard_exclude() -> None:
    """When the negative matches one alias but the positive is a whole
    token of a different, *unshared* alias, the category must survive
    hard-exclusion and appear as a soft-penalised candidate."""

    # audio node has two multi-token aliases; "kulaklık" is the negated
    # concept, "hoparlör" appears as a whole token only in a phrase that
    # does NOT share tokens with the negative.
    catalog, matcher = await _build_stack(
        aliases={
            "audio": ("kablosuz kulaklık", "bluetooth hoparlör"),
            "laptop": ("dizüstü bilgisayar",),
        }
    )
    result = await matcher.match(
        MatchQuery(
            text="kulaklık değil hoparlör lazım",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
            semantic_constraints={
                "positive": [{"concept": "hoparlör", "provenance": "EXPLICIT"}],
                "negative": [
                    {"concept": "kulaklık", "provenance": "EXPLICIT_NEGATION"}
                ],
            },
        )
    )
    slugs = [c.slug for c in result.candidates]
    assert "audio" in slugs, (
        f"catalog-text-compatible positive must rescue the shared category; "
        f"got {slugs!r}"
    )


@pytest.mark.asyncio
async def test_incompatible_positive_does_not_rescue_negative_category() -> None:
    """An unrelated positive concept must NOT keep a negated category
    alive. Only positives with real catalog-text compatibility rescue.

    ``iphone`` is negated and ``dizüstü`` (positive) has zero overlap
    with the mobile category — so mobile is still hard-excluded and
    forbidden=0 safety holds."""

    catalog, matcher = await _build_stack(
        aliases={
            "mobile": ("iphone", "telefon"),
            "laptop": ("laptop", "dizüstü"),
        }
    )
    result = await matcher.match(
        MatchQuery(
            text="iphone değil dizüstü lazım",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
            semantic_constraints={
                "positive": [{"concept": "dizüstü", "provenance": "EXPLICIT"}],
                "negative": [
                    {"concept": "iphone", "provenance": "EXPLICIT_NEGATION"}
                ],
            },
        )
    )
    slugs = [c.slug for c in result.candidates]
    assert "mobile" not in slugs, (
        f"OOS negation with incompatible positive must still hard-exclude; "
        f"got {slugs!r}"
    )
    assert slugs and slugs[0] == "laptop"


@pytest.mark.asyncio
async def test_soft_penalty_ranks_rescued_category_below_correct_one() -> None:
    """When two categories both score, the correct positive-alias node
    ranks strictly above the rescued sibling: negative penalty scaled
    by ``sibling_soft_exclusion_factor`` keeps ranking sane."""

    catalog, matcher = await _build_stack(
        aliases={
            "audio": ("kablosuz kulaklık", "bluetooth hoparlör"),
            "audio_speaker": ("hoparlör", "akıllı hoparlör"),
        },
        policy=SemanticMatchPolicy(
            minimum_candidate_score=0.05,
            minimum_auto_select_score=0.15,
            minimum_auto_select_gap=0.05,
            hard_exclude_exact_negative_alias=True,
            sibling_soft_exclusion_factor=0.20,
        ),
    )
    result = await matcher.match(
        MatchQuery(
            text="kulaklık değil hoparlör lazım",
            catalog_id=catalog.id,
            locale=catalog.primary_locale,
            embedding_profile_id="p1",
            catalog_revision=1,
            semantic_constraints={
                "positive": [{"concept": "hoparlör", "provenance": "EXPLICIT"}],
                "negative": [
                    {"concept": "kulaklık", "provenance": "EXPLICIT_NEGATION"}
                ],
            },
        )
    )
    slugs = [c.slug for c in result.candidates]
    assert "audio_speaker" in slugs
    if "audio" in slugs:
        speaker = next(c.score for c in result.candidates if c.slug == "audio_speaker")
        rescued = next(c.score for c in result.candidates if c.slug == "audio")
        assert speaker > rescued, (
            f"positive-alias node must outrank rescued sibling: "
            f"speaker={speaker:.3f} rescued={rescued:.3f}"
        )
