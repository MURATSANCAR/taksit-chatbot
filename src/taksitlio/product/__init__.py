"""ADR-010 product catalog domain (P1)."""

from taksitlio.product.canonical import resolve_canonical_key
from taksitlio.product.hashing import content_hash
from taksitlio.product.models import (
    FreshnessStatus,
    ProductOfferRecord,
    ProductRecord,
    StockStatus,
)
from taksitlio.product.normalize import normalize_display_name
from taksitlio.product.taxonomy import enrich_product_attributes, taxonomy_code
from taksitlio.product.upsert import (
    OfferUpsertResult,
    ProductUpsertPlan,
    plan_offer_upsert,
    plan_product_upsert,
)

__all__ = [
    "FreshnessStatus",
    "OfferUpsertResult",
    "ProductOfferRecord",
    "ProductRecord",
    "ProductUpsertPlan",
    "StockStatus",
    "content_hash",
    "enrich_product_attributes",
    "normalize_display_name",
    "plan_offer_upsert",
    "plan_product_upsert",
    "resolve_canonical_key",
    "taxonomy_code",
]
