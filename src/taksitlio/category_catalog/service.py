"""Application service for the dynamic category catalog.

All mutations funnel through this service so validation and revision
bookkeeping stay in one place. The service **never** writes to conversation
state; that is the ConversationStateManager's responsibility.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from taksitlio.category_catalog.domain import (
    Alias,
    AttributeLink,
    Catalog,
    CatalogCategory,
    CatalogRevisionRecord,
    CatalogStatus,
    CategorySnapshot,
    CategoryStatus,
    Localization,
    MatchMode,
    PublicationValidationResult,
    RevisionStatus,
    UseCase,
    new_id,
)
from taksitlio.category_catalog.errors import (
    CatalogNotFound,
    CategoryNotFound,
)
from taksitlio.category_catalog.in_memory_repository import (
    InMemoryCategoryCatalogRepository,
)
from taksitlio.category_catalog.policies import (
    DEFAULT_PUBLICATION_RULES,
    PublicationRules,
)
from taksitlio.category_catalog.publication import (
    PublicationView,
    validate_for_publish,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CategoryCatalogService:
    def __init__(
        self,
        repository: InMemoryCategoryCatalogRepository,
        *,
        rules: PublicationRules = DEFAULT_PUBLICATION_RULES,
    ) -> None:
        self._repo = repository
        self._rules = rules

    # ---------------- catalog ----------------

    async def create_catalog(
        self,
        *,
        catalog_code: str,
        display_name: str,
        primary_locale: str = "tr-TR",
        alternate_locales: tuple[str, ...] = (),
        match_policy_code: str = "CATEGORY_MATCH_DEFAULT",
        metadata: Optional[dict] = None,
    ) -> Catalog:
        catalog = Catalog(
            id=new_id(),
            catalog_code=catalog_code,
            display_name=display_name,
            primary_locale=primary_locale,
            alternate_locales=tuple(alternate_locales),
            match_policy_code=match_policy_code,
            status=CatalogStatus.DRAFT,
            metadata=dict(metadata or {}),
        )
        return await self._repo.create_catalog(catalog)

    async def get_catalog(self, catalog_id: str) -> Catalog:
        catalog = await self._repo.get_catalog(catalog_id)
        if catalog is None:
            raise CatalogNotFound(catalog_id)
        return catalog

    # ---------------- categories ----------------

    async def add_category(
        self,
        *,
        catalog_id: str,
        slug: str,
        semantic_description: str,
        parent_id: Optional[str] = None,
        external_code: Optional[str] = None,
        ordering: int = 0,
        metadata: Optional[dict] = None,
    ) -> CatalogCategory:
        catalog = await self.get_catalog(catalog_id)
        depth = 0
        if parent_id:
            parent = await self._repo.get_category(parent_id)
            if parent is None or parent.catalog_id != catalog_id:
                raise CategoryNotFound(parent_id or "")
            depth = parent.depth + 1
        category = CatalogCategory(
            id=new_id(),
            catalog_id=catalog_id,
            slug=slug,
            parent_id=parent_id,
            external_code=external_code,
            depth=depth,
            ordering=ordering,
            status=CategoryStatus.DRAFT,
            semantic_description=semantic_description,
            introduced_revision=catalog.draft_revision,
            metadata=dict(metadata or {}),
        )
        return await self._repo.add_category(category)

    async def activate_category(self, category_id: str) -> CatalogCategory:
        category = await self._require_category(category_id)
        updated = replace(category, status=CategoryStatus.ACTIVE)
        return await self._repo.update_category(updated)

    async def archive_category(self, category_id: str) -> CatalogCategory:
        category = await self._require_category(category_id)
        updated = replace(category, status=CategoryStatus.ARCHIVED)
        return await self._repo.update_category(updated)

    async def deactivate_category(self, category_id: str) -> CatalogCategory:
        category = await self._require_category(category_id)
        updated = replace(category, status=CategoryStatus.INACTIVE)
        return await self._repo.update_category(updated)

    async def _require_category(self, category_id: str) -> CatalogCategory:
        category = await self._repo.get_category(category_id)
        if category is None:
            raise CategoryNotFound(category_id)
        return category

    # ---------------- localization / aliases / use-cases ----------------

    async def add_localization(
        self,
        *,
        category_id: str,
        locale: str,
        display_name: str,
        description: str = "",
        synonyms: tuple[str, ...] = (),
    ) -> Localization:
        await self._require_category(category_id)
        localization = Localization(
            id=new_id(),
            category_id=category_id,
            locale=locale,
            display_name=display_name,
            description=description,
            synonyms=tuple(synonyms),
        )
        return await self._repo.add_localization(localization)

    async def add_alias(
        self,
        *,
        category_id: str,
        locale: str,
        alias_text: str,
        alias_type: MatchMode = MatchMode.EXACT,
        weight: float = 1.0,
    ) -> Alias:
        await self._require_category(category_id)
        alias = Alias(
            id=new_id(),
            category_id=category_id,
            locale=locale,
            alias_text=alias_text,
            alias_type=alias_type,
            weight=max(0.0, min(1.0, float(weight))),
        )
        return await self._repo.add_alias(alias)

    async def add_use_case(
        self,
        *,
        category_id: str,
        locale: str,
        use_case_text: str,
    ) -> UseCase:
        await self._require_category(category_id)
        use_case = UseCase(
            id=new_id(),
            category_id=category_id,
            locale=locale,
            use_case_text=use_case_text,
        )
        return await self._repo.add_use_case(use_case)

    async def add_attribute_link(
        self,
        *,
        category_id: str,
        attribute_definition_id: str,
        importance: float = 0.5,
    ) -> AttributeLink:
        await self._require_category(category_id)
        link = AttributeLink(
            id=new_id(),
            category_id=category_id,
            attribute_definition_id=attribute_definition_id,
            importance=max(0.0, min(1.0, float(importance))),
        )
        return await self._repo.add_attribute_link(link)

    # ---------------- publication ----------------

    async def validate_for_publish(self, catalog_id: str) -> PublicationValidationResult:
        catalog = await self.get_catalog(catalog_id)
        categories = await self._repo.list_categories(catalog_id)
        localizations: list[Localization] = []
        aliases: list[Alias] = []
        use_cases: list[UseCase] = []
        for cat in categories:
            localizations.extend(await self._repo.list_localizations(cat.id))
            aliases.extend(await self._repo.list_aliases(cat.id))
            use_cases.extend(await self._repo.list_use_cases(cat.id))

        # Auto-activate categories that were added as DRAFT but are ready.
        # In real deployment this would be an admin action; keep it explicit
        # here so tests can control status.
        view = PublicationView(
            primary_locale=catalog.primary_locale,
            categories=categories,
            localizations=localizations,
            aliases=aliases,
            use_cases=use_cases,
        )
        return validate_for_publish(view, self._rules)

    async def publish_revision(
        self,
        catalog_id: str,
        *,
        notes: Optional[str] = None,
        auto_activate_drafts: bool = True,
    ) -> Catalog:
        catalog = await self.get_catalog(catalog_id)

        if auto_activate_drafts:
            for category in await self._repo.list_categories(catalog_id):
                if category.status == CategoryStatus.DRAFT:
                    await self._repo.update_category(
                        replace(category, status=CategoryStatus.ACTIVE)
                    )

        validation = await self.validate_for_publish(catalog_id)
        if not validation.ok:
            await self._repo.record_revision(
                CatalogRevisionRecord(
                    id=new_id(),
                    catalog_id=catalog_id,
                    revision=catalog.draft_revision + 1,
                    status=RevisionStatus.DRAFT,
                    validation_report={
                        "issues": list(validation.issues),
                        "warnings": list(validation.warnings),
                    },
                )
            )
            validation.raise_if_invalid()

        next_revision = catalog.draft_revision + 1
        # Build and persist snapshots for primary + alternate locales.
        for locale in (catalog.primary_locale, *catalog.alternate_locales):
            snapshot = await self._repo.build_current_snapshot(
                catalog_id,
                revision=next_revision,
                locale=locale,
            )
            await self._repo.store_snapshot(snapshot)

        updated_catalog = replace(
            catalog,
            status=CatalogStatus.ACTIVE,
            published_revision=next_revision,
            draft_revision=next_revision,
            updated_at=_now(),
        )
        await self._repo.update_catalog(updated_catalog)
        await self._repo.record_revision(
            CatalogRevisionRecord(
                id=new_id(),
                catalog_id=catalog_id,
                revision=next_revision,
                status=RevisionStatus.PUBLISHED,
                published_at=_now(),
                notes=notes,
                validation_report={"warnings": list(validation.warnings)},
            )
        )
        return updated_catalog

    async def get_published_snapshot(
        self,
        catalog_id: str,
        *,
        locale: Optional[str] = None,
    ) -> Optional[CategorySnapshot]:
        catalog = await self._repo.get_catalog(catalog_id)
        if catalog is None:
            return None
        if catalog.published_revision <= 0:
            return None
        snapshot = await self._repo.get_snapshot(
            catalog_id,
            revision=catalog.published_revision,
            locale=locale or catalog.primary_locale,
        )
        return snapshot


__all__ = ["CategoryCatalogService"]
