"""Semantic projection for a catalog category node.

The projector produces a deterministic, locale-aware text used both as the
embedding input and as the source of content_hash-based dedupe.
"""

from __future__ import annotations

from typing import Iterable

from taksitlio.category_catalog.domain import CategorySnapshotNode
from taksitlio.category_embedding.domain import SemanticDocument


def _clean(parts: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in parts:
        text = (raw or "").strip()
        if text:
            result.append(text)
    return result


class CategorySemanticProjector:
    """Produces SemanticDocument objects for a snapshot node."""

    def build_projection_text(self, node: CategorySnapshotNode) -> str:
        segments: list[str] = []
        segments.append(f"category: {node.display_name}")
        if node.description:
            segments.append(f"description: {node.description}")
        if node.semantic_description:
            segments.append(f"summary: {node.semantic_description}")
        synonyms = _clean(node.synonyms)
        if synonyms:
            segments.append("synonyms: " + ", ".join(sorted(set(synonyms))))
        alias_texts = _clean(a.alias_text for a in node.aliases)
        if alias_texts:
            segments.append("aliases: " + ", ".join(sorted(set(alias_texts))))
        use_cases = _clean(u.use_case_text for u in node.use_cases)
        if use_cases:
            segments.append("use_cases: " + " | ".join(use_cases))
        if node.parent_id:
            segments.append(f"parent: {node.parent_id}")
        segments.append(f"locale: {node.locale}")
        return "\n".join(segments)

    def project(
        self,
        node: CategorySnapshotNode,
        *,
        catalog_revision: int,
    ) -> SemanticDocument:
        text = self.build_projection_text(node)
        return SemanticDocument.new(
            category_id=node.id,
            catalog_revision=catalog_revision,
            locale=node.locale,
            projection_text=text,
            metadata={"slug": node.slug, "depth": node.depth},
        )


__all__ = ["CategorySemanticProjector"]
