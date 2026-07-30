"""Errors for the dynamic category catalog domain."""

from __future__ import annotations


class CategoryCatalogError(Exception):
    """Base error for the category catalog."""


class CatalogNotFound(CategoryCatalogError):
    pass


class CategoryNotFound(CategoryCatalogError):
    pass


class CatalogValidationError(CategoryCatalogError):
    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues: list[str] = list(issues or [])


class CatalogPublishRejected(CategoryCatalogError):
    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues: list[str] = list(issues or [])


class CatalogAlreadyExists(CategoryCatalogError):
    pass


class DuplicateAliasError(CategoryCatalogError):
    pass


class CatalogRepositoryUnavailable(CategoryCatalogError):
    pass
