"""Typed domain errors for conversation state."""

from __future__ import annotations


class ConversationStateError(Exception):
    """Base domain error — not an HTTP binding."""

    code: str = "CONVERSATION_STATE_ERROR"


class ConversationSessionNotFound(ConversationStateError):
    code = "SESSION_NOT_FOUND"


class ConversationSessionExpired(ConversationStateError):
    code = "SESSION_EXPIRED"


class ConversationSessionExists(ConversationStateError):
    code = "SESSION_EXISTS"


class ConversationVersionConflict(ConversationStateError):
    code = "VERSION_CONFLICT"

    def __init__(
        self,
        message: str = "Version conflict",
        *,
        expected_revision: int | None = None,
        actual_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class ConversationOutOfOrder(ConversationStateError):
    code = "OUT_OF_ORDER"


class ConversationDuplicateRequest(ConversationStateError):
    code = "DUPLICATE_REQUEST"


class ConversationPatchRejected(ConversationStateError):
    code = "PATCH_REJECTED"


class ConversationStateTooLarge(ConversationStateError):
    code = "STATE_TOO_LARGE"


class ConversationStateValidationError(ConversationStateError):
    code = "STATE_VALIDATION_ERROR"


class ConversationRepositoryUnavailable(ConversationStateError):
    code = "REPOSITORY_UNAVAILABLE"


class ConversationPolicyNotFound(ConversationStateError):
    code = "POLICY_NOT_FOUND"


class ConversationSchemaUnsupported(ConversationStateError):
    code = "SCHEMA_UNSUPPORTED"
