"""Built-in ingestion adapters (opaque codes only)."""

from taksitlio.ingestion.adapters.generic_campaign_feed import (
    ADAPTER_CODE as GENERIC_CAMPAIGN_FEED_V1,
    GenericCampaignFeedAdapter,
)
from taksitlio.ingestion.adapters.generic_json_feed import (
    ADAPTER_CODE as GENERIC_JSON_FEED_V1,
    GenericJsonFeedAdapter,
    register_generic_json_feed,
)

__all__ = [
    "GENERIC_CAMPAIGN_FEED_V1",
    "GENERIC_JSON_FEED_V1",
    "GenericCampaignFeedAdapter",
    "GenericJsonFeedAdapter",
    "register_generic_json_feed",
]
