"""Dynamic category catalog package.

Runtime consumers should import from this module only:
    - CategoryCatalogService (mutations + publication)
    - CategorySnapshot (matcher input)
    - InMemoryCategoryCatalogRepository (tests / integration)

Nothing here binds concrete production category names; the catalog is
DB-driven and revision-versioned.
"""

from taksitlio.category_catalog.domain import (
    Alias,
    AttributeLink,
    Catalog,
    CatalogCategory,
    CatalogRevisionRecord,
    CatalogStatus,
    CategorySnapshot,
    CategorySnapshotNode,
    CategoryStatus,
    Localization,
    MatchMode,
    PublicationValidationResult,
    RevisionStatus,
    UseCase,
)
from taksitlio.category_catalog.errors import (
    CatalogAlreadyExists,
    CatalogEmbeddingsNotReady,
    CatalogNotFound,
    CatalogPublishRejected,
    CatalogRepositoryUnavailable,
    CatalogRevisionNotReady,
    CatalogValidationError,
    CategoryCatalogError,
    CategoryNotFound,
    DuplicateAliasError,
)
from taksitlio.category_catalog.embedding_gate import (
    AlwaysReadyEmbeddingChecker,
    RepositoryEmbeddingReadinessChecker,
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
from taksitlio.category_catalog.service import CategoryCatalogService

__all__ = [
    "AlwaysReadyEmbeddingChecker",
    "CatalogAlreadyExists",
    "CatalogCategory",
    "CatalogEmbeddingsNotReady",
    "CatalogNotFound",
    "CatalogPublishRejected",
    "CatalogRepositoryUnavailable",
    "CatalogRevisionNotReady",
    "CatalogRevisionRecord",
    "CatalogStatus",
    "CatalogValidationError",
    "CategoryCatalogError",
    "CategoryCatalogService",
    "CategoryNotFound",
    "CategorySnapshot",
    "CategorySnapshotNode",
    "CategoryStatus",
    "DEFAULT_PUBLICATION_RULES",
    "DuplicateAliasError",
    "InMemoryCategoryCatalogRepository",
    "Localization",
    "MatchMode",
    "PublicationRules",
    "PublicationValidationResult",
    "PublicationView",
    "RepositoryEmbeddingReadinessChecker",
    "RevisionStatus",
    "UseCase",
    "validate_for_publish",
]
