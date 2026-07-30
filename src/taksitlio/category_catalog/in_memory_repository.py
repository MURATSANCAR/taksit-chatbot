"""In-memory repository for the dynamic category catalog.

Fully functional for unit tests and for the mandatory dynamic-runtime
integration test. This is not production storage.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from typing import Optional, Sequence

from taksitlio.category_catalog.domain import (
    Alias,
    AttributeLink,
    Catalog,
    CatalogCategory,
    CatalogRevisionRecord,
    CategorySnapshot,
    CategorySnapshotNode,
    CategoryStatus,
    Localization,
    UseCase,
)
from taksitlio.category_catalog.errors import (
    CatalogAlreadyExists,
    CategoryNotFound,
)


class InMemoryCategoryCatalogRepository:
    def __init__(self) -> None:
        self._catalogs: dict[str, Catalog] = {}
        self._categories: dict[str, CatalogCategory] = {}
        self._localizations: dict[str, Localization] = {}
        self._aliases: dict[str, Alias] = {}
        self._use_cases: dict[str, UseCase] = {}
        self._attribute_links: dict[str, AttributeLink] = {}
        self._revisions: dict[str, list[CatalogRevisionRecord]] = {}
        # published snapshots keyed by (catalog_id, revision, locale)
        self._snapshots: dict[tuple[str, int, str], CategorySnapshot] = {}
        self._lock = asyncio.Lock()

    async def create_catalog(self, catalog: Catalog) -> Catalog:
        async with self._lock:
            for existing in self._catalogs.values():
                if existing.catalog_code == catalog.catalog_code:
                    raise CatalogAlreadyExists(
                        f"Catalog already exists: {catalog.catalog_code}"
                    )
            stored = replace(catalog)
            self._catalogs[stored.id] = stored
            self._revisions.setdefault(stored.id, [])
            return stored

    async def get_catalog(self, catalog_id: str) -> Optional[Catalog]:
        async with self._lock:
            return self._catalogs.get(catalog_id)

    async def get_catalog_by_code(self, catalog_code: str) -> Optional[Catalog]:
        async with self._lock:
            for c in self._catalogs.values():
                if c.catalog_code == catalog_code:
                    return c
            return None

    async def update_catalog(self, catalog: Catalog) -> Catalog:
        async with self._lock:
            if catalog.id not in self._catalogs:
                raise CategoryNotFound(catalog.id)
            self._catalogs[catalog.id] = catalog
            return catalog

    async def add_category(self, category: CatalogCategory) -> CatalogCategory:
        async with self._lock:
            self._categories[category.id] = category
            return category

    async def update_category(self, category: CatalogCategory) -> CatalogCategory:
        async with self._lock:
            if category.id not in self._categories:
                raise CategoryNotFound(category.id)
            self._categories[category.id] = category
            return category

    async def get_category(self, category_id: str) -> Optional[CatalogCategory]:
        async with self._lock:
            return self._categories.get(category_id)

    async def list_categories(self, catalog_id: str) -> list[CatalogCategory]:
        async with self._lock:
            return [c for c in self._categories.values() if c.catalog_id == catalog_id]

    async def add_localization(self, localization: Localization) -> Localization:
        async with self._lock:
            self._localizations[localization.id] = localization
            return localization

    async def list_localizations(self, category_id: str) -> list[Localization]:
        async with self._lock:
            return [l for l in self._localizations.values() if l.category_id == category_id]

    async def add_alias(self, alias: Alias) -> Alias:
        async with self._lock:
            self._aliases[alias.id] = alias
            return alias

    async def list_aliases(self, category_id: str) -> list[Alias]:
        async with self._lock:
            return [a for a in self._aliases.values() if a.category_id == category_id]

    async def deactivate_alias(self, alias_id: str) -> None:
        async with self._lock:
            existing = self._aliases.get(alias_id)
            if existing is None:
                return
            self._aliases[alias_id] = replace(existing, status=CategoryStatus.INACTIVE)

    async def add_use_case(self, use_case: UseCase) -> UseCase:
        async with self._lock:
            self._use_cases[use_case.id] = use_case
            return use_case

    async def list_use_cases(self, category_id: str) -> list[UseCase]:
        async with self._lock:
            return [u for u in self._use_cases.values() if u.category_id == category_id]

    async def add_attribute_link(self, link: AttributeLink) -> AttributeLink:
        async with self._lock:
            self._attribute_links[link.id] = link
            return link

    async def record_revision(self, revision: CatalogRevisionRecord) -> None:
        async with self._lock:
            self._revisions.setdefault(revision.catalog_id, []).append(revision)

    async def list_active_snapshot_categories(
        self,
        catalog_id: str,
        revision: int,
        locale: str,
    ) -> Sequence[CatalogCategory]:
        async with self._lock:
            return [
                c
                for c in self._categories.values()
                if c.catalog_id == catalog_id and c.status == CategoryStatus.ACTIVE
            ]

    async def store_snapshot(
        self,
        snapshot: CategorySnapshot,
    ) -> None:
        async with self._lock:
            self._snapshots[
                (snapshot.catalog_id, snapshot.revision, snapshot.locale)
            ] = snapshot

    async def get_snapshot(
        self,
        catalog_id: str,
        *,
        revision: Optional[int] = None,
        locale: Optional[str] = None,
    ) -> Optional[CategorySnapshot]:
        async with self._lock:
            catalog = self._catalogs.get(catalog_id)
            if catalog is None:
                return None
            resolved_revision = (
                revision if revision is not None else catalog.published_revision
            )
            resolved_locale = locale or catalog.primary_locale
            snap = self._snapshots.get(
                (catalog_id, resolved_revision, resolved_locale)
            )
            return snap

    def _build_snapshot_now(
        self,
        catalog_id: str,
        revision: int,
        locale: str,
    ) -> CategorySnapshot:
        """Build a snapshot from the current mutable state (called under lock)."""

        catalog = self._catalogs[catalog_id]
        by_id = {
            c.id: c
            for c in self._categories.values()
            if c.catalog_id == catalog_id and c.status == CategoryStatus.ACTIVE
        }
        localizations_by_cat: dict[str, Localization] = {}
        for l in self._localizations.values():
            if l.locale != locale or l.status == CategoryStatus.INACTIVE:
                continue
            if l.category_id in by_id:
                localizations_by_cat[l.category_id] = l

        aliases_by_cat: dict[str, list[Alias]] = {}
        for a in self._aliases.values():
            if a.locale != locale or a.status != CategoryStatus.ACTIVE:
                continue
            if a.category_id in by_id:
                aliases_by_cat.setdefault(a.category_id, []).append(a)

        use_cases_by_cat: dict[str, list[UseCase]] = {}
        for u in self._use_cases.values():
            if u.locale != locale or u.status != CategoryStatus.ACTIVE:
                continue
            if u.category_id in by_id:
                use_cases_by_cat.setdefault(u.category_id, []).append(u)

        nodes: list[CategorySnapshotNode] = []
        for cat in by_id.values():
            localization = localizations_by_cat.get(cat.id)
            if localization is None:
                continue
            ancestors = self._collect_ancestors(cat.id, by_id)
            depth = len(ancestors)
            nodes.append(
                CategorySnapshotNode(
                    id=cat.id,
                    catalog_id=catalog_id,
                    slug=cat.slug,
                    parent_id=cat.parent_id if cat.parent_id in by_id else None,
                    depth=depth,
                    display_name=localization.display_name,
                    description=localization.description,
                    semantic_description=cat.semantic_description,
                    synonyms=tuple(localization.synonyms),
                    aliases=tuple(aliases_by_cat.get(cat.id, [])),
                    use_cases=tuple(use_cases_by_cat.get(cat.id, [])),
                    locale=locale,
                    ancestor_ids=ancestors,
                )
            )

        nodes.sort(key=lambda n: (n.depth, n.slug))
        return CategorySnapshot(
            catalog_id=catalog_id,
            catalog_code=catalog.catalog_code,
            revision=revision,
            primary_locale=catalog.primary_locale,
            locale=locale,
            match_policy_code=catalog.match_policy_code,
            nodes=tuple(nodes),
        )

    def _collect_ancestors(
        self,
        category_id: str,
        by_id: dict[str, CatalogCategory],
    ) -> tuple[str, ...]:
        chain: list[str] = []
        current = by_id.get(category_id)
        seen: set[str] = set()
        while current and current.parent_id and current.parent_id not in seen:
            chain.append(current.parent_id)
            seen.add(current.parent_id)
            current = by_id.get(current.parent_id)
        return tuple(chain)

    async def build_current_snapshot(
        self,
        catalog_id: str,
        *,
        revision: int,
        locale: str,
    ) -> CategorySnapshot:
        async with self._lock:
            return self._build_snapshot_now(catalog_id, revision, locale)

    async def snapshot_size(self) -> int:
        async with self._lock:
            return len(self._snapshots)

    # Test-only helpers ------------------------------------------------

    async def dump_state(self) -> dict:
        async with self._lock:
            return {
                "catalogs": deepcopy(self._catalogs),
                "categories": deepcopy(self._categories),
                "localizations": deepcopy(self._localizations),
                "aliases": deepcopy(self._aliases),
                "use_cases": deepcopy(self._use_cases),
                "snapshots": deepcopy(self._snapshots),
            }


__all__ = ["InMemoryCategoryCatalogRepository"]
