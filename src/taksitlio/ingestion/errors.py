"""Typed ingestion failure codes (ADR-010 §65)."""

from __future__ import annotations


class IngestionError(Exception):
    """Base typed ingestion error."""

    code: str = "UNKNOWN"

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        super().__init__(message or self.code)
        self.detail = detail


class SourceTimeout(IngestionError):
    code = "SOURCE_TIMEOUT"


class SourceBlocked(IngestionError):
    code = "SOURCE_BLOCKED"


class SourceSchemaChanged(IngestionError):
    code = "SOURCE_SCHEMA_CHANGED"


class ProductParseFailed(IngestionError):
    code = "PRODUCT_PARSE_FAILED"


class MediaFetchFailed(IngestionError):
    code = "MEDIA_FETCH_FAILED"


class CampaignParseFailed(IngestionError):
    code = "CAMPAIGN_PARSE_FAILED"


class RateUnavailable(IngestionError):
    code = "RATE_UNAVAILABLE"


class AuthFailed(IngestionError):
    code = "AUTH_FAILED"


class RateLimited(IngestionError):
    code = "RATE_LIMITED"


__all__ = [
    "AuthFailed",
    "CampaignParseFailed",
    "IngestionError",
    "MediaFetchFailed",
    "ProductParseFailed",
    "RateLimited",
    "RateUnavailable",
    "SourceBlocked",
    "SourceSchemaChanged",
    "SourceTimeout",
]
