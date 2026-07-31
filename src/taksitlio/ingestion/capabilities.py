"""Typed ingestion capabilities (ADR-010 §33)."""

from __future__ import annotations

from enum import Enum


class IngestionCapability(str, Enum):
    PRODUCT_DISCOVERY = "PRODUCT_DISCOVERY"
    PRODUCT_DETAIL = "PRODUCT_DETAIL"
    PRICE = "PRICE"
    STOCK = "STOCK"
    MEDIA = "MEDIA"
    CATEGORY = "CATEGORY"
    ATTRIBUTE = "ATTRIBUTE"
    CAMPAIGN = "CAMPAIGN"
    FINANCE_OPTION = "FINANCE_OPTION"
    BRANCH_AVAILABILITY = "BRANCH_AVAILABILITY"


__all__ = ["IngestionCapability"]
