"""Category quality-dimension applicability (policy data, not category-name code)."""

from __future__ import annotations

from enum import Enum
from typing import Mapping, Optional


class QualityDimension(str, Enum):
    BRAND = "BRAND"
    CATEGORY = "CATEGORY"
    CRITICAL_ATTRIBUTES = "CRITICAL_ATTRIBUTES"
    STOCK = "STOCK"
    CARD_MEDIA = "CARD_MEDIA"
    PRICE = "PRICE"
    PRODUCT_URL = "PRODUCT_URL"
    FINANCE = "FINANCE"
    PAYMENT_PLAN = "PAYMENT_PLAN"
    GTIN = "GTIN"
    MPN = "MPN"
    PUBLISHER = "PUBLISHER"
    AUTHOR = "AUTHOR"
    ISBN = "ISBN"
    MODEL = "MODEL"
    VARIANT = "VARIANT"


class DimensionApplicability(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SOURCE_DEPENDENT = "SOURCE_DEPENDENT"


def resolve_dimension_applicability(
    *,
    default_dimensions: Mapping[str, object],
    category_overrides: Mapping[str, object],
    category_id: Optional[int],
    dimension: QualityDimension | str,
) -> DimensionApplicability:
    """Resolve applicability for one dimension from versioned policy maps."""

    key = dimension.value if isinstance(dimension, QualityDimension) else str(dimension)
    resolved: object = default_dimensions.get(key, DimensionApplicability.OPTIONAL.value)
    if category_id is not None:
        ov = category_overrides.get(str(category_id)) or category_overrides.get(category_id)  # type: ignore[index]
        if isinstance(ov, Mapping) and key in ov:
            resolved = ov[key]
    try:
        return DimensionApplicability(str(resolved))
    except ValueError:
        return DimensionApplicability.OPTIONAL


def dimension_enters_denominator(app: DimensionApplicability) -> bool:
    """REQUIRED and SOURCE_DEPENDENT enter readiness denominator; N/A does not."""

    return app in {
        DimensionApplicability.REQUIRED,
        DimensionApplicability.SOURCE_DEPENDENT,
    }


def dimension_blocks_scope(app: DimensionApplicability) -> bool:
    return app is DimensionApplicability.REQUIRED


__all__ = [
    "DimensionApplicability",
    "QualityDimension",
    "dimension_blocks_scope",
    "dimension_enters_denominator",
    "resolve_dimension_applicability",
]
