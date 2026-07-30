"""Typed patch engine — allowlisted paths, immutable apply, domain validation."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from jsonschema import Draft202012Validator

from taksitlio.conversation_state.domain import (
    ActiveNeed,
    CategoryResolution,
    ClarificationState,
    ConversationState,
    SessionStatus,
)
from taksitlio.conversation_state.errors import (
    ConversationPatchRejected,
    ConversationStateTooLarge,
    ConversationStateValidationError,
)
from taksitlio.conversation_state.policies import ConversationStatePolicy
from taksitlio.conversation_state.serialization import dumps_canonical

_FORBIDDEN_NEED_SEED_KEYS = frozenset(
    {
        "need_id",
        "category_resolution",
        "selected_category_id",
        "session_id",
        "revision",
        "schema_version",
        "created_at",
        "updated_at",
        "expires_at",
        "absolute_expires_at",
        "actor",
        "status",
        "metadata",
        "last_client_message_id",
        "last_client_sequence",
        "clarification",
    }
)


@lru_cache(maxsize=1)
def _need_seed_validator() -> Draft202012Validator:
    path = (
        Path(__file__).resolve().parents[1] / "schemas" / "need_seed.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate_need_seed(seed: Mapping[str, Any]) -> None:
    forbidden = _FORBIDDEN_NEED_SEED_KEYS.intersection(seed.keys())
    if forbidden:
        raise ConversationPatchRejected(
            f"need_profile forbids platform/category fields: {sorted(forbidden)}"
        )
    errors = sorted(_need_seed_validator().iter_errors(dict(seed)), key=lambda e: e.path)
    if errors:
        messages = "; ".join(e.message for e in errors[:5])
        raise ConversationPatchRejected(f"need_profile schema invalid: {messages}")

ALLOWED_OPERATIONS = frozenset(
    {"SET", "REMOVE", "APPEND", "REPLACE_COLLECTION", "RESET_NEED"}
)

# Model-writable path families (JSON Pointer prefixes / exact paths)
ALLOWED_PATH_PREFIXES = (
    "/active_need/intent",
    "/active_need/need_description",
    "/active_need/budget",
    "/active_need/preferences",
    "/active_need/usage_context",
    "/active_need/entities",
    "/active_need/ambiguities",
    "/active_need/category_resolution",
    "/active_need/confidence",
    "/active_need/signals",
    "/clarification",
    "/resolved_context",
)

FORBIDDEN_PLATFORM_PREFIXES = (
    "/session_id",
    "/revision",
    "/schema_version",
    "/created_at",
    "/updated_at",
    "/expires_at",
    "/absolute_expires_at",
    "/actor",
    "/status",
    "/last_client_message_id",
    "/last_client_sequence",
    "/metadata",
)


class PatchEngine:
    def validate_patch_document(self, patch: Mapping[str, Any]) -> None:
        if "old_value" in patch or "updates" in patch or "field" in patch:
            raise ConversationPatchRejected(
                "Legacy fields (old_value/updates/field) are not accepted"
            )
        operation = patch.get("operation")
        if operation not in ALLOWED_OPERATIONS:
            raise ConversationPatchRejected(f"Unsupported operation: {operation}")
        if operation == "RESET_NEED":
            return
        path = patch.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ConversationPatchRejected("path must be a JSON Pointer starting with /")
        self._assert_path_allowed(path)

    def apply(
        self,
        state: ConversationState,
        patch: Mapping[str, Any],
        *,
        policy: ConversationStatePolicy,
        source_message_id: str | None = None,
    ) -> ConversationState:
        self.validate_patch_document(patch)
        working = state.copy()
        operation = str(patch["operation"])

        if operation == "RESET_NEED":
            working.active_need = ActiveNeed.empty()
            seed = patch.get("need_profile")
            if seed is None and isinstance(patch.get("value"), Mapping):
                # value may carry typed seed only when it validates as need_seed
                seed = patch.get("value")
            if seed is not None:
                if not isinstance(seed, Mapping):
                    raise ConversationPatchRejected("need_profile must be an object")
                _validate_need_seed(seed)
                seeded = ActiveNeed.from_dict(
                    {**dict(seed), "need_id": working.active_need.need_id}
                )
                working.active_need = seeded
            working.clarification = ClarificationState()
            working.status = SessionStatus.ACTIVE
            if working.active_need:
                working.active_need = ActiveNeed(
                    need_id=working.active_need.need_id,
                    intent=working.active_need.intent,
                    need_description=working.active_need.need_description,
                    budget=working.active_need.budget,
                    preferences=working.active_need.preferences,
                    usage_context=working.active_need.usage_context,
                    entities=working.active_need.entities,
                    ambiguities=working.active_need.ambiguities,
                    category_resolution=CategoryResolution(),
                    confidence=working.active_need.confidence,
                    signals=working.active_need.signals,
                )
            self._maybe_store_evidence_ref(working, patch, source_message_id)
            self.validate_state(working, policy)
            return working

        path = str(patch["path"])
        payload = working.to_payload_dict()

        if operation == "SET":
            if "value" not in patch:
                raise ConversationPatchRejected("SET requires value")
            _set_pointer(payload, path, deepcopy(patch.get("value")))
        elif operation == "REMOVE":
            _remove_pointer(payload, path)
        elif operation == "APPEND":
            existing = _get_pointer(payload, path)
            if existing is None:
                existing = []
                _set_pointer(payload, path, existing)
            if not isinstance(existing, list):
                raise ConversationPatchRejected(f"APPEND target is not a list: {path}")
            existing.append(deepcopy(patch.get("value")))
        elif operation == "REPLACE_COLLECTION":
            value = patch.get("value")
            if not isinstance(value, list):
                raise ConversationPatchRejected("REPLACE_COLLECTION requires list value")
            _set_pointer(payload, path, deepcopy(value))
        else:
            raise ConversationPatchRejected(f"Unsupported operation: {operation}")

        # Rehydrate; platform fields restored from original working copy below
        mutated = ConversationState.from_payload_dict(payload)
        mutated.session_id = working.session_id
        mutated.schema_version = working.schema_version
        mutated.revision = working.revision
        mutated.status = working.status
        mutated.locale = working.locale
        mutated.actor = working.actor
        mutated.created_at = working.created_at
        mutated.updated_at = working.updated_at
        mutated.expires_at = working.expires_at
        mutated.absolute_expires_at = working.absolute_expires_at
        mutated.last_client_message_id = working.last_client_message_id
        mutated.last_client_sequence = working.last_client_sequence
        mutated.metadata = dict(working.metadata)

        self._maybe_store_evidence_ref(mutated, patch, source_message_id)
        self.validate_state(mutated, policy)
        return mutated

    def validate_state(
        self,
        state: ConversationState,
        policy: ConversationStatePolicy,
    ) -> None:
        raw = dumps_canonical(state.to_payload_dict())
        size = len(raw.encode("utf-8"))
        if size > policy.max_state_size_bytes:
            raise ConversationStateTooLarge(
                f"State size {size} exceeds max_state_size_bytes={policy.max_state_size_bytes}"
            )

        meta_raw = dumps_canonical(state.metadata)
        if len(meta_raw.encode("utf-8")) > policy.max_metadata_bytes:
            raise ConversationStateTooLarge("metadata exceeds max_metadata_bytes")

        if "transcript" in state.metadata or "chat_history" in state.metadata:
            raise ConversationPatchRejected(
                "Raw transcript must not be stored in session metadata"
            )

        need = state.active_need
        if need is None:
            return

        if len(need.need_description) > policy.max_string_length:
            raise ConversationStateValidationError("need_description too long")
        if len(need.preferences) > policy.max_preferences:
            raise ConversationStateTooLarge("preferences exceed max_preferences")
        if len(need.entities) > policy.max_entities:
            raise ConversationStateTooLarge("entities exceed max_entities")
        if len(need.ambiguities) > policy.max_ambiguities:
            raise ConversationStateTooLarge("ambiguities exceed max_ambiguities")
        if len(need.category_resolution.candidates) > policy.max_category_candidates:
            raise ConversationStateTooLarge("category candidates exceed limit")

        budget = need.budget or {}
        if budget:
            _validate_budget(budget)

    def _assert_path_allowed(self, path: str) -> None:
        for forbidden in FORBIDDEN_PLATFORM_PREFIXES:
            if path == forbidden or path.startswith(forbidden + "/"):
                raise ConversationPatchRejected(f"Platform path is not writable: {path}")
        if path == "/active_need" or path == "/active_need/need_id":
            raise ConversationPatchRejected(f"Path not allowlisted: {path}")
        for prefix in ALLOWED_PATH_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return
        raise ConversationPatchRejected(f"Path not allowlisted: {path}")

    @staticmethod
    def _maybe_store_evidence_ref(
        state: ConversationState,
        patch: Mapping[str, Any],
        source_message_id: str | None,
    ) -> None:
        evidence = patch.get("evidence_text")
        if not evidence:
            return
        digest = hashlib.sha256(str(evidence).encode("utf-8")).hexdigest()[:32]
        refs = dict(state.metadata.get("evidence_refs") or {})
        refs[digest] = {
            "source_message_id": source_message_id,
            "operation": patch.get("operation"),
            "path": patch.get("path"),
        }
        # Keep bounded
        if len(refs) > 16:
            # drop arbitrary oldest-ish by sorted keys
            for key in sorted(refs.keys())[: len(refs) - 16]:
                refs.pop(key, None)
        state.metadata["evidence_refs"] = refs


def _validate_budget(budget: Mapping[str, Any]) -> None:
    def _num(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError) as exc:
            raise ConversationStateValidationError("budget values must be numeric") from exc

    minimum = _num(budget.get("minimum"))
    maximum = _num(budget.get("maximum"))
    value = _num(budget.get("value"))
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ConversationStateValidationError("budget.minimum > budget.maximum")
    if value is not None and maximum is not None and value > maximum:
        raise ConversationStateValidationError("budget.value > budget.maximum")
    if value is not None and minimum is not None and value < minimum:
        raise ConversationStateValidationError("budget.value < budget.minimum")


def _pointer_parts(path: str) -> list[str]:
    return [p.replace("~1", "/").replace("~0", "~") for p in path.lstrip("/").split("/")]


def _set_pointer(target: dict[str, Any], path: str, value: Any) -> None:
    parts = _pointer_parts(path)
    node: Any = target
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _get_pointer(target: dict[str, Any], path: str) -> Any:
    node: Any = target
    for part in _pointer_parts(path):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _remove_pointer(target: dict[str, Any], path: str) -> None:
    parts = _pointer_parts(path)
    node: Any = target
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    if isinstance(node, dict):
        node.pop(parts[-1], None)
