"""Repository protocol for the dynamic category catalog."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence

from taksitlio.category_catalog.domain import (
    Alias,
    AttributeLink,
    Catalog,
    CatalogCategory,
    CatalogRevisionRecord,
    CategorySnapshot,
    Localization,
    UseCase,
)


class CategoryCatalogRepository(Protocol):
    async def create_catalog(self, catalog: Catalog) -> Catalog: ...

    async def get_catalog(self, catalog_id: str) -> Optional[Catalog]: ...

    async def get_catalog_by_code(self, catalog_code: str) -> Optional[Catalog]: ...

    async def update_catalog(self, catalog: Catalog) -> Catalog: ...

    async def add_category(self, category: CatalogCategory) -> CatalogCategory: ...

    async def update_category(self, category: CatalogCategory) -> CatalogCategory: ...

    async def get_category(self, category_id: str) -> Optional[CatalogCategory]: ...

    async def list_categories(self, catalog_id: str) -> list[CatalogCategory]: ...

    async def add_localization(self, localization: Localization) -> Localization: ...

    async def list_localizations(self, category_id: str) -> list[Localization]: ...

    async def add_alias(self, alias: Alias) -> Alias: ...

    async def list_aliases(self, category_id: str) -> list[Alias]: ...

    async def deactivate_alias(self, alias_id: str) -> None: ...

    async def add_use_case(self, use_case: UseCase) -> UseCase: ...

    async def list_use_cases(self, category_id: str) -> list[UseCase]: ...

    async def add_attribute_link(self, link: AttributeLink) -> AttributeLink: ...

    async def record_revision(self, revision: CatalogRevisionRecord) -> None: ...

    async def get_snapshot(
        self,
        catalog_id: str,
        *,
        revision: Optional[int] = None,
        locale: Optional[str] = None,
    ) -> Optional[CategorySnapshot]: ...

    async def list_active_snapshot_categories(
        self,
        catalog_id: str,
        revision: int,
        locale: str,
    ) -> Sequence[CatalogCategory]: ...


__all__ = ["CategoryCatalogRepository"]
