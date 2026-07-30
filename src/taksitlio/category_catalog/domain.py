"""Dynamic category catalog domain models.

Nothing in this module names concrete production categories. Everything the
matcher reads is loaded from a repository (in-memory for tests, Postgres in
production). Category identifiers are UUIDs represented as strings; the
domain never leaks internal integer IDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


class CatalogStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class CategoryStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class MatchMode(str, Enum):
    EXACT = "EXACT"
    PREFIX = "PREFIX"
    FUZZY = "FUZZY"
    SEMANTIC_HINT = "SEMANTIC_HINT"


class RevisionStatus(str, Enum):
    """Two-stage publish lifecycle for a catalog revision."""

    DRAFT = "DRAFT"
    PREPARING = "PREPARING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"  # retained for historical rows


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class Localization:
    id: str
    category_id: str
    locale: str
    display_name: str
    description: str = ""
    synonyms: tuple[str, ...] = ()
    status: CategoryStatus = CategoryStatus.ACTIVE


@dataclass(frozen=True)
class Alias:
    id: str
    category_id: str
    locale: str
    alias_text: str
    alias_type: MatchMode = MatchMode.EXACT
    weight: float = 1.0
    status: CategoryStatus = CategoryStatus.ACTIVE


@dataclass(frozen=True)
class UseCase:
    id: str
    category_id: str
    locale: str
    use_case_text: str
    status: CategoryStatus = CategoryStatus.ACTIVE


@dataclass(frozen=True)
class AttributeLink:
    id: str
    category_id: str
    attribute_definition_id: str  # UUID reference; external table
    importance: float = 0.5
    status: CategoryStatus = CategoryStatus.ACTIVE


@dataclass(frozen=True)
class CatalogCategory:
    id: str
    catalog_id: str
    slug: str
    parent_id: Optional[str] = None
    external_code: Optional[str] = None
    depth: int = 0
    ordering: int = 0
    status: CategoryStatus = CategoryStatus.DRAFT
    semantic_description: str = ""
    introduced_revision: int = 0
    retired_revision: Optional[int] = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Catalog:
    id: str
    catalog_code: str
    display_name: str
    primary_locale: str = "tr-TR"
    alternate_locales: tuple[str, ...] = ()
    match_policy_code: str = "CATEGORY_MATCH_DEFAULT"
    status: CatalogStatus = CatalogStatus.DRAFT
    published_revision: int = 0
    draft_revision: int = 0
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class CatalogRevisionRecord:
    id: str
    catalog_id: str
    revision: int
    status: RevisionStatus
    published_at: Optional[datetime] = None
    notes: Optional[str] = None
    validation_report: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class CategorySnapshotNode:
    """Runtime-ready view of a single category for the matcher."""

    id: str
    catalog_id: str
    slug: str
    parent_id: Optional[str]
    depth: int
    display_name: str
    description: str
    semantic_description: str
    synonyms: tuple[str, ...]
    aliases: tuple[Alias, ...]
    use_cases: tuple[UseCase, ...]
    locale: str
    ancestor_ids: tuple[str, ...]
    # False for out-of-scope / non-product nodes — may retrieve but never MATCHED.
    matchable: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CategorySnapshot:
    """Immutable, publish-time snapshot for the matcher."""

    catalog_id: str
    catalog_code: str
    revision: int
    primary_locale: str
    locale: str
    match_policy_code: str
    nodes: tuple[CategorySnapshotNode, ...]

    @property
    def is_empty(self) -> bool:
        return not self.nodes

    def get(self, category_id: str) -> Optional[CategorySnapshotNode]:
        for node in self.nodes:
            if node.id == category_id:
                return node
        return None


@dataclass(frozen=True)
class PublicationValidationResult:
    ok: bool
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def raise_if_invalid(self) -> None:
        if not self.ok:
            from taksitlio.category_catalog.errors import CatalogPublishRejected

            raise CatalogPublishRejected(
                "; ".join(self.issues), issues=list(self.issues)
            )


__all__ = [
    "Alias",
    "AttributeLink",
    "Catalog",
    "CatalogCategory",
    "CatalogRevisionRecord",
    "CatalogStatus",
    "CategorySnapshot",
    "CategorySnapshotNode",
    "CategoryStatus",
    "Localization",
    "MatchMode",
    "PublicationValidationResult",
    "RevisionStatus",
    "UseCase",
    "new_id",
]
