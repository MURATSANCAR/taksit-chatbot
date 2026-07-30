"""Errors for the semantic category matcher."""

from __future__ import annotations


class SemanticMatchingError(Exception):
    """Base error for semantic category matching."""


class EmbeddingGatewayUnavailable(SemanticMatchingError):
    pass


class CatalogUnavailable(SemanticMatchingError):
    pass


__all__ = [
    "CatalogUnavailable",
    "EmbeddingGatewayUnavailable",
    "SemanticMatchingError",
]
