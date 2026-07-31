"""Built-in ingestion adapters (opaque codes only)."""

from taksitlio.ingestion.adapters.generic_json_feed import (
    ADAPTER_CODE as GENERIC_JSON_FEED_V1,
    GenericJsonFeedAdapter,
    register_generic_json_feed,
)

__all__ = [
    "GENERIC_JSON_FEED_V1",
    "GenericJsonFeedAdapter",
    "register_generic_json_feed",
]
