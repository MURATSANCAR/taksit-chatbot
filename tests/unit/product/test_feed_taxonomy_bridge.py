"""Feed brand/category → attributes, taxonomy match, and multi-field search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taksitlio.ingestion.adapters.generic_json_feed import GenericJsonFeedAdapter
from taksitlio.ingestion.capabilities import IngestionCapability
from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
from taksitlio.product.catalog import InMemoryProductCatalogRepository
from taksitlio.product.taxonomy import (
    enrich_product_attributes,
    pick_existing_category,
    taxonomy_code,
)
from taksitlio.product.upsert import plan_offer_upsert, plan_product_upsert
from taksitlio.product_query.candidates import load_search_candidates_from_catalog
from taksitlio.query_understanding import fast_parse
from taksitlio.search_sessions.catalog_pool import (
    apply_catalog_hints,
    brands_from_pool,
    candidate_to_pool_dict,
    categories_from_pool,
)
from taksitlio.search_sessions.orchestrator import build_empty_orchestrator


def test_taxonomy_code_and_enrich_attributes() -> None:
    assert taxonomy_code("Spor Ayakkabı") == "SPOR_AYAKKABI"
    attrs = enrich_product_attributes(
        {"ram_gb": 16},
        brand_name="Nike",
        model_number="Air Max",
        category_name="Ayakkabı",
    )
    assert attrs["brand"] == "Nike"
    assert attrs["model"] == "Air Max"
    assert attrs["category"] == "Ayakkabı"
    assert attrs["ram_gb"] == 16


def test_pick_existing_category_prefers_synonym_overlap() -> None:
    cats = [
        {
            "id": 1,
            "category_code": "FOOTWEAR",
            "display_name": "Ayakkabı",
            "synonyms": ("ayakkabı", "sneaker"),
        }
    ]
    hit = pick_existing_category("Kadın Spor Ayakkabı", categories=cats)
    assert hit is not None
    assert hit["category_code"] == "FOOTWEAR"


def test_plan_product_upsert_copies_taxonomy_into_attributes() -> None:
    plan = plan_product_upsert(
        NormalizedProduct(
            external_product_id="s1",
            display_name="Nike Air Max 90",
            brand_name="Nike",
            model_number="Air Max 90",
            category_name="Ayakkabı",
        )
    )
    assert plan.category_name == "Ayakkabı"
    assert plan.attributes["brand"] == "Nike"
    assert plan.attributes["category"] == "Ayakkabı"


@pytest.mark.asyncio
async def test_generic_feed_reads_category(tmp_path: Path) -> None:
    path = tmp_path / "feed.json"
    path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "S1",
                        "name": "Nike Air Max 90",
                        "brand": "Nike",
                        "model": "Air Max 90",
                        "category": "Ayakkabı",
                        "price": 4500,
                        "stock_status": "AVAILABLE",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    adapter = GenericJsonFeedAdapter(feed_path=path)
    assert IngestionCapability.CATEGORY in adapter.capabilities()
    product = await adapter.fetch_product("S1")
    assert product.brand_name == "Nike"
    assert product.category_name == "Ayakkabı"


@pytest.mark.asyncio
async def test_search_matches_category_attr_not_only_display_name() -> None:
    catalog = InMemoryProductCatalogRepository()
    plan = plan_product_upsert(
        NormalizedProduct(
            external_product_id="nike-1",
            display_name="Nike Air Max 90",
            brand_name="Nike",
            model_number="Air Max 90",
            category_name="Ayakkabı",
        )
    )
    stored = await catalog.upsert_product(
        merchant_id=1,
        plan=plan,
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=1,
        product_id=stored.id,
        plan=plan_offer_upsert(
            NormalizedOffer(external_product_id="nike-1", current_price=4500.0),
            NormalizedStock(external_product_id="nike-1", stock_status="AVAILABLE"),
        ),
    )

    hits = await catalog.list_products_matching(name_terms=("ayakkabı",), merchant_id=1)
    assert len(hits) == 1
    assert hits[0].id == stored.id

    brand_hits = await catalog.list_products_matching(name_terms=("Nike",), merchant_id=1)
    assert len(brand_hits) == 1

    cands = await load_search_candidates_from_catalog(
        catalog, utterance="ayakkabı istiyorum", merchant_id=1
    )
    assert len(cands) == 1
    assert cands[0].brand_name == "Nike"
    assert cands[0].category_name == "Ayakkabı"

    pool = [candidate_to_pool_dict(c) for c in cands]
    brands = brands_from_pool(pool)
    assert any(b.display_name == "Nike" for b in brands)
    feed_cats = categories_from_pool(pool)
    assert any(c.display_name == "Ayakkabı" for c in feed_cats)

    orch = build_empty_orchestrator()
    apply_catalog_hints(orch, categories=feed_cats, brands=brands)
    parse = fast_parse("bana ayakkabı lazım 5000 lira aşmasın", catalog=orch.catalog)
    assert any(
        "ayakkab" in c.display_name.casefold() or c.resolved_id == "AYAKKABI"
        for c in parse.positive_categories
    )
