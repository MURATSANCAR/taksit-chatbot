"""Fixture catalog builder for evaluation runs.

Reads ``evaluation/fixtures/catalogs/category-fixture.vN.json`` and
publishes an isolated catalog via the canonical two-stage publish
helper (``prepare_embed_and_publish``). The public artefact is a
``FixtureCatalog`` object exposing the fixture-key → UUID map plus
the catalog id and revision the matcher stack should read.

Nothing in this module references production category identifiers.
Callers must dispose of the fixture catalog once the run finishes;
the ``dispose`` helper on ``FixtureCatalog`` archives the fixture
categories so subsequent test runs start clean.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from taksitlio.category_catalog import (
    CategoryCatalogService,
    InMemoryCategoryCatalogRepository,
    MatchMode,
    RepositoryEmbeddingReadinessChecker,
)
from taksitlio.category_embedding import (
    CategoryEmbeddingOutbox,
    CategoryEmbeddingWorker,
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.category_catalog.publish_pipeline import prepare_embed_and_publish
from taksitlio.embeddings.client import LexicalEmbedder
from taksitlio.evaluation.errors import (
    FixtureCatalogError,
    UnknownFixtureKeyError,
)


DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "fixtures"
    / "catalogs"
    / "category-fixture.v1.json"
)


@dataclass(frozen=True)
class FixtureCatalog:
    """Wired evaluation catalog handle.

    ``key_to_uuid`` never leaks slugs to the runtime; test code should
    only use fixture keys and this handle to resolve UUIDs.
    """

    catalog_id: str
    revision: int
    locale: str
    embedding_profile_id: str
    service: CategoryCatalogService
    embedding_repository: InMemoryCategoryEmbeddingRepository
    key_to_uuid: dict[str, str]
    uuid_to_key: dict[str, str]

    def resolve(self, fixture_key: str) -> str:
        cid = self.key_to_uuid.get(fixture_key)
        if cid is None:
            raise UnknownFixtureKeyError(fixture_key)
        return cid

    def reverse(self, category_uuid: str) -> Optional[str]:
        return self.uuid_to_key.get(category_uuid)


class _LexicalEmbeddingClient:
    def __init__(self, dim: int = 64) -> None:
        self._embedder = LexicalEmbedder(dim=dim)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embedder.embed(list(texts))


def _load_fixture_document(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if "categories" not in data or not isinstance(data["categories"], list):
        raise FixtureCatalogError(
            f"fixture catalog {path.name}: 'categories' array missing"
        )
    return data


def _topo_sort_by_parent(entries: list[dict]) -> list[dict]:
    """Sort fixture entries so parents come before children.

    Falls back to source order for entries with no parent.
    """

    by_key = {entry["fixture_key"]: entry for entry in entries}
    visited: set[str] = set()
    ordered: list[dict] = []

    def _visit(key: str, stack: set[str]) -> None:
        if key in visited:
            return
        if key in stack:
            raise FixtureCatalogError(
                f"parent-child cycle in fixture catalog at '{key}'"
            )
        stack.add(key)
        entry = by_key[key]
        parent = entry.get("parent_fixture_key")
        if parent:
            if parent not in by_key:
                raise FixtureCatalogError(
                    f"unknown parent '{parent}' for fixture '{key}'"
                )
            _visit(parent, stack)
        stack.discard(key)
        visited.add(key)
        ordered.append(entry)

    for entry in entries:
        _visit(entry["fixture_key"], set())
    return ordered


async def build_fixture_catalog(
    *,
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
    catalog_code: str = "FIXTURE",
    embedding_profile_id: str = "fixture-p1",
    embedding_dim: int = 64,
) -> FixtureCatalog:
    """Build and publish the isolated evaluation catalog.

    The catalog uses the canonical two-stage publish pipeline
    (``prepare_embed_and_publish``); the returned handle is ready for
    the matcher stack to consume.
    """

    document = _load_fixture_document(fixture_path)

    catalog_repo = InMemoryCategoryCatalogRepository()
    service = CategoryCatalogService(catalog_repo)
    catalog = await service.create_catalog(
        catalog_code=catalog_code,
        display_name=document.get("catalog_id", "Fixture"),
        primary_locale=document.get("primary_locale", "tr-TR"),
        match_policy_code=document.get(
            "match_policy_code", "CATEGORY_MATCH_FIXTURE"
        ),
    )

    key_to_uuid: dict[str, str] = {}
    uuid_to_key: dict[str, str] = {}

    # Order: parents first, then children. Topological sort by parent_fixture_key.
    ordered_entries: list[dict] = _topo_sort_by_parent(document["categories"])

    for entry in ordered_entries:
        fixture_key = entry["fixture_key"]
        if not fixture_key.startswith("fixture."):
            raise FixtureCatalogError(
                f"fixture key must start with 'fixture.': {fixture_key}"
            )
        parent_fixture_key = entry.get("parent_fixture_key")
        parent_id: str | None = None
        if parent_fixture_key:
            parent_id = key_to_uuid.get(parent_fixture_key)
            if parent_id is None:
                raise FixtureCatalogError(
                    f"parent fixture key '{parent_fixture_key}' not found before "
                    f"'{fixture_key}'"
                )
        cat = await service.add_category(
            catalog_id=catalog.id,
            slug=entry["slug"],
            semantic_description=entry.get("semantic_description", ""),
            parent_id=parent_id,
        )
        key_to_uuid[fixture_key] = cat.id
        uuid_to_key[cat.id] = fixture_key

        loc = entry.get("localization") or {}
        await service.add_localization(
            category_id=cat.id,
            locale=catalog.primary_locale,
            display_name=loc.get("display_name", entry["slug"].title()),
            description=loc.get("description", ""),
            synonyms=tuple(loc.get("synonyms") or ()),
        )
        for alias in entry.get("aliases") or ():
            await service.add_alias(
                category_id=cat.id,
                locale=catalog.primary_locale,
                alias_text=alias["text"],
                alias_type=MatchMode(alias.get("type", "EXACT")),
            )
        for uc in entry.get("use_cases") or ():
            await service.add_use_case(
                category_id=cat.id,
                locale=catalog.primary_locale,
                use_case_text=uc,
            )

    embedding_repo = InMemoryCategoryEmbeddingRepository()
    outbox = CategoryEmbeddingOutbox(embedding_repo)
    worker = CategoryEmbeddingWorker(
        embedding_repo, _LexicalEmbeddingClient(dim=embedding_dim)
    )

    revision = await prepare_embed_and_publish(
        service,
        catalog.id,
        outbox,
        worker,
        embedding_profile_id=embedding_profile_id,
        embedding_repo=embedding_repo,
    )

    return FixtureCatalog(
        catalog_id=catalog.id,
        revision=revision,
        locale=catalog.primary_locale,
        embedding_profile_id=embedding_profile_id,
        service=service,
        embedding_repository=embedding_repo,
        key_to_uuid=key_to_uuid,
        uuid_to_key=uuid_to_key,
    )


async def dispose_fixture_catalog(handle: FixtureCatalog) -> None:
    """Archive every fixture category — used as test teardown.

    Since the fixture catalog is fully in-memory this is defensive:
    archiving mirrors what a Postgres-backed run would do (the
    isolation contract in ADR-005 §4).
    """

    for uuid in list(handle.uuid_to_key):
        try:
            await handle.service.archive_category(uuid)
        except Exception:  # noqa: BLE001 — best-effort teardown
            continue


def all_fixture_keys(path: Path = DEFAULT_FIXTURE_PATH) -> list[str]:
    document = _load_fixture_document(path)
    return [c["fixture_key"] for c in document["categories"]]


__all__ = [
    "DEFAULT_FIXTURE_PATH",
    "FixtureCatalog",
    "all_fixture_keys",
    "build_fixture_catalog",
    "dispose_fixture_catalog",
]
