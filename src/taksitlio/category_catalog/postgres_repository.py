"""Postgres repository stub for the dynamic category catalog.

The MVP tests use the in-memory repository. This module defines the wire
signatures for the production adapter so downstream code can depend on it
without pulling asyncpg into unit tests.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

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
from taksitlio.category_catalog.errors import CatalogRepositoryUnavailable


class PostgresCategoryCatalogRepository:
    """Placeholder repository backed by asyncpg (wired in production only)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def _unavailable(self, op: str):
        raise CatalogRepositoryUnavailable(
            f"PostgresCategoryCatalogRepository.{op} not implemented in MVP"
        )

    async def create_catalog(self, catalog: Catalog) -> Catalog:
        await self._unavailable("create_catalog")
        return catalog

    async def get_catalog(self, catalog_id: str) -> Optional[Catalog]:
        await self._unavailable("get_catalog")
        return None

    async def get_catalog_by_code(self, catalog_code: str) -> Optional[Catalog]:
        await self._unavailable("get_catalog_by_code")
        return None

    async def update_catalog(self, catalog: Catalog) -> Catalog:
        await self._unavailable("update_catalog")
        return catalog

    async def add_category(self, category: CatalogCategory) -> CatalogCategory:
        await self._unavailable("add_category")
        return category

    async def update_category(self, category: CatalogCategory) -> CatalogCategory:
        await self._unavailable("update_category")
        return category

    async def get_category(self, category_id: str) -> Optional[CatalogCategory]:
        await self._unavailable("get_category")
        return None

    async def list_categories(self, catalog_id: str) -> list[CatalogCategory]:
        await self._unavailable("list_categories")
        return []

    async def add_localization(self, localization: Localization) -> Localization:
        await self._unavailable("add_localization")
        return localization

    async def list_localizations(self, category_id: str) -> list[Localization]:
        await self._unavailable("list_localizations")
        return []

    async def add_alias(self, alias: Alias) -> Alias:
        await self._unavailable("add_alias")
        return alias

    async def list_aliases(self, category_id: str) -> list[Alias]:
        await self._unavailable("list_aliases")
        return []

    async def deactivate_alias(self, alias_id: str) -> None:
        await self._unavailable("deactivate_alias")

    async def add_use_case(self, use_case: UseCase) -> UseCase:
        await self._unavailable("add_use_case")
        return use_case

    async def list_use_cases(self, category_id: str) -> list[UseCase]:
        await self._unavailable("list_use_cases")
        return []

    async def add_attribute_link(self, link: AttributeLink) -> AttributeLink:
        await self._unavailable("add_attribute_link")
        return link

    async def record_revision(self, revision: CatalogRevisionRecord) -> None:
        await self._unavailable("record_revision")

    async def get_snapshot(
        self,
        catalog_id: str,
        *,
        revision: Optional[int] = None,
        locale: Optional[str] = None,
    ) -> Optional[CategorySnapshot]:
        await self._unavailable("get_snapshot")
        return None

    async def list_active_snapshot_categories(
        self,
        catalog_id: str,
        revision: int,
        locale: str,
    ) -> Sequence[CatalogCategory]:
        await self._unavailable("list_active_snapshot_categories")
        return []


__all__ = ["PostgresCategoryCatalogRepository"]
