"""Apply conversation UPDATE operations onto Redis session need state."""

from __future__ import annotations

from taksitlio.conversation.patch import (
    ALLOWED_OPERATIONS,
    ALLOWED_PATHS,
    ConversationPatchError,
    apply_conversation_patch,
    apply_conversation_update,
)

__all__ = [
    "ALLOWED_OPERATIONS",
    "ALLOWED_PATHS",
    "ConversationPatchError",
    "apply_conversation_patch",
    "apply_conversation_update",
]
