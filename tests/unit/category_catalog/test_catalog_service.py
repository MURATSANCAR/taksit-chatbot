"""Unit tests for CategoryCatalogService (dynamic catalog + publication)."""

from __future__ import annotations

import pytest

from taksitlio.category_catalog import (
    CatalogPublishRejected,
    CategoryCatalogService,
    CategoryStatus,
    InMemoryCategoryCatalogRepository,
    MatchMode,
    PublicationRules,
)


async def _fresh_service(**kwargs) -> tuple[CategoryCatalogService, InMemoryCategoryCatalogRepository]:
    repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(repo, **kwargs)
    return service, repo


@pytest.mark.asyncio
async def test_create_catalog_produces_uuid_and_draft_status():
    service, _ = await _fresh_service()
    catalog = await service.create_catalog(
        catalog_code="TEST",
        display_name="Test",
    )
    assert catalog.id
    assert catalog.status.value == "DRAFT"
    assert catalog.published_revision == 0


@pytest.mark.asyncio
async def test_add_category_requires_existing_catalog():
    service, _ = await _fresh_service()
    with pytest.raises(Exception):
        await service.add_category(
            catalog_id="00000000-0000-0000-0000-000000000000",
            slug="x",
            semantic_description="test",
        )


@pytest.mark.asyncio
async def test_publish_rejects_missing_localization_or_empty_semantic():
    service, _ = await _fresh_service()
    catalog = await service.create_catalog(catalog_code="C1", display_name="C1")
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="dummy",
        semantic_description="",  # empty on purpose
    )
    # Add only a non-primary locale — should still fail on primary_locale rule.
    await service.add_localization(
        category_id=category.id,
        locale="en-US",
        display_name="Dummy",
    )
    with pytest.raises(CatalogPublishRejected) as exc_info:
        await service.publish_revision(catalog.id)
    issues = "; ".join(exc_info.value.issues)
    assert "semantic_description" in issues or "primary" in issues.lower()


@pytest.mark.asyncio
async def test_publish_success_makes_snapshot_available():
    service, repo = await _fresh_service()
    catalog = await service.create_catalog(catalog_code="C2", display_name="C2")
    category = await service.add_category(
        catalog_id=catalog.id,
        slug="dummy",
        semantic_description="A description that summarises the category",
    )
    await service.add_localization(
        category_id=category.id,
        locale=catalog.primary_locale,
        display_name="Dummy",
        description="Localized description",
        synonyms=("s1", "s2"),
    )
    await service.add_alias(
        category_id=category.id,
        locale=catalog.primary_locale,
        alias_text="alias-one",
    )
    await service.add_use_case(
        category_id=category.id,
        locale=catalog.primary_locale,
        use_case_text="testing dynamic catalog",
    )
    published = await service.publish_revision(catalog.id)
    assert published.published_revision == 1
    snapshot = await service.get_published_snapshot(catalog.id)
    assert snapshot is not None
    assert len(snapshot.nodes) == 1
    node = snapshot.nodes[0]
    assert node.slug == "dummy"
    assert node.display_name == "Dummy"
    assert "s1" in node.synonyms
    assert node.aliases[0].alias_text == "alias-one"


@pytest.mark.asyncio
async def test_parent_cycle_rejected():
    rules = PublicationRules(require_semantic_description=False)
    service, _ = await _fresh_service(rules=rules)
    catalog = await service.create_catalog(catalog_code="CY", display_name="CY")
    a = await service.add_category(
        catalog_id=catalog.id, slug="a", semantic_description=""
    )
    b = await service.add_category(
        catalog_id=catalog.id, slug="b", parent_id=a.id, semantic_description=""
    )
    await service.add_localization(
        category_id=a.id, locale=catalog.primary_locale, display_name="A"
    )
    await service.add_localization(
        category_id=b.id, locale=catalog.primary_locale, display_name="B"
    )
    # Force a cycle by mutating parent directly on repository.
    from dataclasses import replace as _replace

    from taksitlio.category_catalog.domain import CategoryStatus

    cycled_a = _replace(a, parent_id=b.id, status=CategoryStatus.ACTIVE)
    cycled_b = _replace(b, status=CategoryStatus.ACTIVE)
    await service._repo.update_category(cycled_a)
    await service._repo.update_category(cycled_b)

    validation = await service.validate_for_publish(catalog.id)
    assert not validation.ok
    assert any("cycle" in issue for issue in validation.issues)


@pytest.mark.asyncio
async def test_duplicate_active_alias_rejected():
    rules = PublicationRules(require_semantic_description=False)
    service, _ = await _fresh_service(rules=rules)
    catalog = await service.create_catalog(catalog_code="DUP", display_name="DUP")
    a = await service.add_category(
        catalog_id=catalog.id, slug="a", semantic_description=""
    )
    b = await service.add_category(
        catalog_id=catalog.id, slug="b", semantic_description=""
    )
    await service.add_localization(
        category_id=a.id, locale=catalog.primary_locale, display_name="A"
    )
    await service.add_localization(
        category_id=b.id, locale=catalog.primary_locale, display_name="B"
    )
    await service.add_alias(
        category_id=a.id, locale=catalog.primary_locale, alias_text="shared"
    )
    await service.add_alias(
        category_id=b.id, locale=catalog.primary_locale, alias_text="Shared"
    )
    with pytest.raises(CatalogPublishRejected) as exc_info:
        await service.publish_revision(catalog.id)
    assert any("duplicate active alias" in i for i in exc_info.value.issues)


@pytest.mark.asyncio
async def test_archive_hides_category_from_snapshot():
    service, _ = await _fresh_service()
    catalog = await service.create_catalog(catalog_code="AR", display_name="AR")
    a = await service.add_category(
        catalog_id=catalog.id, slug="keep", semantic_description="kept"
    )
    b = await service.add_category(
        catalog_id=catalog.id, slug="drop", semantic_description="dropped"
    )
    for cat in (a, b):
        await service.add_localization(
            category_id=cat.id,
            locale=catalog.primary_locale,
            display_name=cat.slug.title(),
        )
    await service.publish_revision(catalog.id)
    await service.archive_category(b.id)
    await service.publish_revision(catalog.id)
    snapshot = await service.get_published_snapshot(catalog.id)
    assert snapshot is not None
    slugs = {n.slug for n in snapshot.nodes}
    assert "keep" in slugs
    assert "drop" not in slugs


@pytest.mark.asyncio
async def test_get_snapshot_returns_none_before_publish():
    service, _ = await _fresh_service()
    catalog = await service.create_catalog(catalog_code="NP", display_name="NP")
    snapshot = await service.get_published_snapshot(catalog.id)
    assert snapshot is None
