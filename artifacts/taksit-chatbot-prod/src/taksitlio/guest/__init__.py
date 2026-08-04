"""Guest (loginsiz) session entry and needs-analysis flow for Taksitlio chatbot.

Production-grade implementation of the consumer mobile app unauthenticated
entry point described in the product use-case:

  1. Bot proactively offers needs analysis.
  2. User replies in free text (intent + budget).
  3. System extracts → matches category → ranks campaigns by budget fit.
  4. Returns top 1-2 grounded recommendations + MembershipCTA.
"""

from .entry import GuestEntryHandler, GuestTurnResult
from .needs_analysis import NeedsAnalysisService

__all__ = [
    "GuestEntryHandler",
    "GuestTurnResult",
    "NeedsAnalysisService",
]
