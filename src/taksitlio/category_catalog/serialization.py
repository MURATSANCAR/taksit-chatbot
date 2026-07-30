"""Serialization helpers for the dynamic category catalog."""

from __future__ import annotations

from typing import Any

from taksitlio.category_catalog.domain import (
    Alias,
    Catalog,
    CatalogCategory,
    CategorySnapshot,
    CategorySnapshotNode,
    Localization,
    UseCase,
)


def catalog_to_dict(catalog: Catalog) -> dict[str, Any]:
    return {
        "id": catalog.id,
        "catalog_code": catalog.catalog_code,
        "display_name": catalog.display_name,
        "primary_locale": catalog.primary_locale,
        "alternate_locales": list(catalog.alternate_locales),
        "match_policy_code": catalog.match_policy_code,
        "status": catalog.status.value,
        "published_revision": catalog.published_revision,
        "draft_revision": catalog.draft_revision,
        "metadata": dict(catalog.metadata),
    }


def category_to_dict(category: CatalogCategory) -> dict[str, Any]:
    return {
        "id": category.id,
        "catalog_id": category.catalog_id,
        "parent_id": category.parent_id,
        "external_code": category.external_code,
        "slug": category.slug,
        "depth": category.depth,
        "ordering": category.ordering,
        "status": category.status.value,
        "semantic_description": category.semantic_description,
        "introduced_revision": category.introduced_revision,
        "retired_revision": category.retired_revision,
        "metadata": dict(category.metadata),
    }


def localization_to_dict(localization: Localization) -> dict[str, Any]:
    return {
        "id": localization.id,
        "category_id": localization.category_id,
        "locale": localization.locale,
        "display_name": localization.display_name,
        "description": localization.description,
        "synonyms": list(localization.synonyms),
        "status": localization.status.value,
    }


def alias_to_dict(alias: Alias) -> dict[str, Any]:
    return {
        "id": alias.id,
        "category_id": alias.category_id,
        "locale": alias.locale,
        "alias_text": alias.alias_text,
        "alias_type": alias.alias_type.value,
        "weight": alias.weight,
        "status": alias.status.value,
    }


def use_case_to_dict(use_case: UseCase) -> dict[str, Any]:
    return {
        "id": use_case.id,
        "category_id": use_case.category_id,
        "locale": use_case.locale,
        "use_case_text": use_case.use_case_text,
        "status": use_case.status.value,
    }


def snapshot_to_safe_dict(snapshot: CategorySnapshot) -> dict[str, Any]:
    """Only publish-safe fields (no embeddings, no internal jobs)."""

    return {
        "catalog_id": snapshot.catalog_id,
        "catalog_code": snapshot.catalog_code,
        "revision": snapshot.revision,
        "locale": snapshot.locale,
        "primary_locale": snapshot.primary_locale,
        "match_policy_code": snapshot.match_policy_code,
        "nodes": [_node_to_dict(n) for n in snapshot.nodes],
    }


def _node_to_dict(node: CategorySnapshotNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "slug": node.slug,
        "parent_id": node.parent_id,
        "depth": node.depth,
        "locale": node.locale,
        "display_name": node.display_name,
        "description": node.description,
        "synonyms": list(node.synonyms),
        "aliases": [alias_to_dict(a) for a in node.aliases],
        "use_cases": [use_case_to_dict(u) for u in node.use_cases],
    }


__all__ = [
    "alias_to_dict",
    "catalog_to_dict",
    "category_to_dict",
    "localization_to_dict",
    "snapshot_to_safe_dict",
    "use_case_to_dict",
]
