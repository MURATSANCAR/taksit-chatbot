"""Publication validation for category catalog revisions.

Validation is pure: it runs against a snapshot-like view built by the service
and never mutates the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from taksitlio.category_catalog.domain import (
    Alias,
    CatalogCategory,
    CategoryStatus,
    Localization,
    PublicationValidationResult,
    UseCase,
)
from taksitlio.category_catalog.policies import PublicationRules


@dataclass(frozen=True)
class PublicationView:
    """Convenience container used by the validator."""

    primary_locale: str
    categories: Sequence[CatalogCategory]
    localizations: Sequence[Localization]
    aliases: Sequence[Alias]
    use_cases: Sequence[UseCase]


def validate_for_publish(
    view: PublicationView,
    rules: PublicationRules,
) -> PublicationValidationResult:
    issues: list[str] = []
    warnings: list[str] = []

    by_id = {c.id: c for c in view.categories}
    active_categories = [
        c for c in view.categories if c.status == CategoryStatus.ACTIVE
    ]

    if rules.forbid_parent_cycle:
        for category in active_categories:
            visited: set[str] = set()
            current = category
            while current.parent_id:
                if current.parent_id in visited:
                    issues.append(
                        f"parent cycle detected in category {category.slug}"
                    )
                    break
                visited.add(current.parent_id)
                parent = by_id.get(current.parent_id)
                if parent is None:
                    break
                current = parent
                if current.id == category.id:
                    issues.append(
                        f"parent cycle detected in category {category.slug}"
                    )
                    break

    if rules.forbid_orphan_nonroot:
        for category in active_categories:
            if category.parent_id and category.parent_id not in by_id:
                issues.append(
                    f"orphan category {category.slug} — parent missing"
                )

    if rules.forbid_archived_parent_active_child:
        for category in active_categories:
            parent_id = category.parent_id
            if not parent_id:
                continue
            parent = by_id.get(parent_id)
            if parent is None:
                continue
            if parent.status in {CategoryStatus.ARCHIVED, CategoryStatus.INACTIVE}:
                issues.append(
                    f"category {category.slug} has non-active parent {parent.slug}"
                )

    if rules.max_depth:
        for category in active_categories:
            depth = _compute_depth(category, by_id)
            if depth > rules.max_depth:
                issues.append(
                    f"category {category.slug} exceeds max_depth={rules.max_depth}"
                )

    if rules.require_semantic_description:
        for category in active_categories:
            if not (category.semantic_description or "").strip():
                issues.append(
                    f"category {category.slug} has empty semantic_description"
                )

    if rules.require_primary_locale_localization:
        localized_by_cat = _group(view.localizations, key=lambda x: x.category_id)
        for category in active_categories:
            locales = {
                l.locale
                for l in localized_by_cat.get(category.id, [])
                if l.status != CategoryStatus.INACTIVE
            }
            if view.primary_locale not in locales:
                issues.append(
                    f"category {category.slug} missing localization for "
                    f"{view.primary_locale}"
                )

    if rules.forbid_duplicate_active_alias:
        seen: dict[tuple[str, str, str], str] = {}
        for alias in view.aliases:
            if alias.status != CategoryStatus.ACTIVE:
                continue
            cat = by_id.get(alias.category_id)
            if cat is None or cat.status != CategoryStatus.ACTIVE:
                continue
            key = (cat.catalog_id, alias.locale, alias.alias_text.strip().lower())
            existing = seen.get(key)
            if existing and existing != alias.category_id:
                issues.append(
                    f"duplicate active alias '{alias.alias_text}' in locale "
                    f"{alias.locale}"
                )
            else:
                seen.setdefault(key, alias.category_id)

    ok = not issues
    return PublicationValidationResult(
        ok=ok,
        issues=tuple(issues),
        warnings=tuple(warnings),
    )


def _compute_depth(
    category: CatalogCategory,
    by_id: dict[str, CatalogCategory],
) -> int:
    depth = 0
    current = category
    while current.parent_id and depth < 64:  # hard safety cap
        parent = by_id.get(current.parent_id)
        if parent is None:
            break
        depth += 1
        current = parent
    return depth


def _group(items: Iterable, key):
    grouped: dict = {}
    for item in items:
        grouped.setdefault(key(item), []).append(item)
    return grouped


__all__ = ["PublicationView", "validate_for_publish"]
