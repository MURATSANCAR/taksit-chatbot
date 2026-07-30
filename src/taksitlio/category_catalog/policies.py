"""Validation configuration for catalog publication."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicationRules:
    """Rules used by validate_for_publish. All configurable, none hardcoded."""

    require_semantic_description: bool = True
    require_primary_locale_localization: bool = True
    max_depth: int = 4
    forbid_archived_parent_active_child: bool = True
    forbid_orphan_nonroot: bool = True
    forbid_duplicate_active_alias: bool = True
    forbid_parent_cycle: bool = True


DEFAULT_PUBLICATION_RULES = PublicationRules()


__all__ = ["PublicationRules", "DEFAULT_PUBLICATION_RULES"]
