"""TEST-only catalog hints for Query Golden parser lane (ADR-013).

Loads entity aliases from evaluation/fixtures JSON (data), not hardcoded
query→entity maps in production source.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from taksitlio.entity_resolution import EntityCandidate
from taksitlio.query_understanding import CatalogHints


def _fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "evaluation"
        / "fixtures"
        / "query_golden_test_catalog.json"
    )


def _entities(rows: list[dict]) -> tuple[EntityCandidate, ...]:
    out: list[EntityCandidate] = []
    for row in rows:
        out.append(
            EntityCandidate(
                entity_id=str(row["entity_id"]),
                display_name=str(row["display_name"]),
                canonical_name=str(row["canonical_name"]),
                aliases=tuple(str(a) for a in (row.get("aliases") or ())),
                entity_type=str(row.get("entity_type") or "unknown"),
            )
        )
    return tuple(out)


@lru_cache(maxsize=1)
def build_query_golden_test_catalog() -> CatalogHints:
    """Broad fixture covering merchants/banks/categories used in golden v1."""

    data = json.loads(_fixture_path().read_text(encoding="utf-8"))
    return CatalogHints(
        merchants=_entities(list(data.get("merchants") or [])),
        categories=_entities(list(data.get("categories") or [])),
        brands=_entities(list(data.get("brands") or [])),
        institutions=_entities(list(data.get("institutions") or [])),
    )
