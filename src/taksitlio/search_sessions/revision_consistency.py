"""Search session revision pinning — no mixed revisions in one answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SearchRevisionBundle:
    catalog_revision: str
    entity_index_revision: str
    finance_revision: str
    ranking_policy_version: str
    offer_revision: Optional[str] = None
    media_revision: Optional[str] = None

    def fingerprint(self) -> str:
        return "|".join(
            [
                self.catalog_revision,
                self.entity_index_revision,
                self.finance_revision,
                self.ranking_policy_version,
            ]
        )


@dataclass(frozen=True)
class RevisionConsistencyResult:
    consistent: bool
    reasons: tuple[str, ...]
    session: SearchRevisionBundle
    attempted: Optional[SearchRevisionBundle]


def assert_revision_consistency(
    session: SearchRevisionBundle,
    attempted: SearchRevisionBundle,
) -> RevisionConsistencyResult:
    """Mid-query catalog changes must not mix into the active session answer."""

    reasons: list[str] = []
    if session.catalog_revision != attempted.catalog_revision:
        reasons.append("catalog_revision_mismatch")
    if session.entity_index_revision != attempted.entity_index_revision:
        reasons.append("entity_index_revision_mismatch")
    if session.finance_revision != attempted.finance_revision:
        reasons.append("finance_revision_mismatch")
    if session.ranking_policy_version != attempted.ranking_policy_version:
        reasons.append("ranking_policy_version_mismatch")
    return RevisionConsistencyResult(
        consistent=not reasons,
        reasons=tuple(reasons),
        session=session,
        attempted=attempted,
    )


def pin_session_revisions(bundle: SearchRevisionBundle) -> dict[str, str]:
    return {
        "catalog_revision": bundle.catalog_revision,
        "entity_index_revision": bundle.entity_index_revision,
        "finance_revision": bundle.finance_revision,
        "ranking_policy_version": bundle.ranking_policy_version,
    }


__all__ = [
    "RevisionConsistencyResult",
    "SearchRevisionBundle",
    "assert_revision_consistency",
    "pin_session_revisions",
]
