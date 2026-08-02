"""Hydrate search orchestrator from production catalog (ADR-011 / ADR-010).

Categories, merchants, brands, and institutions come from DB/catalog sources —
never from hardcoded demo electronics lists.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Protocol, Sequence

from taksitlio.category.matcher import Category
from taksitlio.entity_resolution import EntityCandidate
from taksitlio.media.logo_resolver import LogoResolver
from taksitlio.merchant.directory import MerchantDirectory
from taksitlio.product.catalog import ProductCatalogRepository
from taksitlio.product_query.candidates import load_search_candidates_from_catalog
from taksitlio.product_query.finance_index import FinanceOptionIndex, InstitutionLabelResolver
from taksitlio.progressive_results.category_match import legacy_family_includes
from taksitlio.query_understanding import CatalogHints
from taksitlio.search_sessions.orchestrator import SearchOrchestrator

# Process-local hydrate caches (single API worker). Short TTLs keep catalog fresh.
_HINTS_TTL_S = 120.0
_POOL_TTL_S = 90.0
_hints_cache: dict[str, Any] = {"ts": 0.0, "categories": None, "merchants": None}
_pool_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_POOL_CACHE_MAX = 128
# Single-flight: coalesce concurrent cache-miss hydrates for the same revision+key.
_inflight_pools: dict[str, asyncio.Future[list[dict[str, Any]]]] = {}
_inflight_hints: Optional[asyncio.Future[tuple[list[Category], list[EntityCandidate]]]] = None
_apply_lock = asyncio.Lock()
# Verbs/filler stripped from term-cache key so near-duplicate asks share a pool.
_CACHE_STOPWORDS = frozenset(
    {
        "arıyorum",
        "ariyorum",
        "bakıyorum",
        "bakiyorum",
        "istiyorum",
        "lazım",
        "lazim",
        "gerek",
        "alacağım",
        "alacagim",
        "almak",
        "öner",
        "oner",
        "tavsiye",
        "bana",
        "bir",
        "tane",
        "lütfen",
        "lutfen",
        "merhaba",
        "selam",
    }
)


def _pool_cache_key(
    utterance: str,
    limit: int,
    terms: Sequence[str] = (),
    *,
    catalog_revision: str = "",
    prefer_search_ready: bool = False,
) -> str:
    """Key by revision + search terms so near-duplicates share pools safely."""

    cleaned = [
        " ".join(str(t).casefold().split())
        for t in terms
        if t and str(t).strip() and str(t).casefold() not in _CACHE_STOPWORDS
    ]
    rev = (catalog_revision or "unknown").strip() or "unknown"
    prefix = f"{'sr' if prefer_search_ready else 'full'}|{rev}|{limit}|"
    if cleaned:
        return prefix + "terms:" + "|".join(sorted(set(cleaned)))
    from taksitlio.semantic_matching.turkish_normalize import turkish_lower

    tokens = [
        tok
        for tok in (turkish_lower(utterance or "") or "").split()
        if tok and tok not in _CACHE_STOPWORDS and len(tok) >= 2
    ]
    return prefix + "utt:" + " ".join(tokens)[:160]


def cache_stats() -> dict[str, Any]:
    return {
        "pool_cache_entries": len(_pool_cache),
        "inflight_pools": len(_inflight_pools),
        "hints_age_s": max(0.0, time.monotonic() - float(_hints_cache.get("ts") or 0.0)),
    }


class CategoryListSource(Protocol):
    async def list_active(self) -> list[Category]: ...


def candidate_to_pool_dict(cand: Any) -> dict[str, Any]:
    finance = None
    if getattr(cand, "card_finance", None) is not None:
        f = cand.card_finance
        finance = {
            "institution_display_name": f.institution_display_name,
            "term_months": f.term_months,
            "monthly_payment": f.monthly_payment,
            "total_repayment": f.total_repayment,
            "display_label": f.display_label,
            "institution_logo_cdn_url": getattr(f, "institution_logo_cdn_url", None),
        }
    brand = getattr(cand, "brand_name", None)
    if not brand and getattr(cand, "brand_model", None):
        brand = str(cand.brand_model).split("/")[0].strip() or None
    return {
        "product_id": cand.product_id,
        "display_name": cand.display_name,
        "brand": brand,
        "brand_model": cand.brand_model,
        "category": getattr(cand, "category_name", None),
        "merchant_id": cand.merchant_id,
        "merchant_display_name": cand.merchant_display_name,
        "price": cand.price,
        "stock_status": cand.stock_status,
        "price_freshness": cand.price_freshness,
        "has_primary_image": cand.has_primary_image,
        "thumbnail_cdn_url": cand.thumbnail_cdn_url,
        "merchant_logo_cdn_url": cand.merchant_logo_cdn_url,
        "query_relevance": cand.query_relevance,
        "attribute_coverage": cand.attribute_coverage,
        "finance_active": cand.finance_active,
        "rate_fresh": cand.rate_fresh,
        "best_monthly_payment": cand.best_monthly_payment,
        "best_total_repayment": cand.best_total_repayment,
        "best_term_months": getattr(cand, "best_term_months", None)
        or (finance.get("term_months") if isinstance(finance, dict) else None),
        "best_finance": finance,
    }


def category_to_entity(cat: Category) -> EntityCandidate:
    aliases = tuple(
        dict.fromkeys(
            [
                *(cat.synonyms or ()),
                cat.display_name,
                cat.category_code.replace("_", " ").lower(),
            ]
        )
    )
    return EntityCandidate(
        entity_id=str(cat.category_code),
        display_name=cat.display_name,
        canonical_name=cat.category_code,
        aliases=aliases,
        entity_type="category",
    )


def category_include_tokens(cat: Category) -> tuple[str, ...]:
    """Tokens used to filter product haystacks for a resolved category."""

    tokens: list[str] = []
    for raw in (cat.display_name, *(cat.synonyms or ())):
        t = str(raw or "").casefold().strip()
        if t:
            tokens.append(t)
    legacy = legacy_family_includes(cat.category_code)
    tokens.extend(legacy)
    return tuple(dict.fromkeys(tokens))


def brands_from_pool(pool: Sequence[dict[str, Any]]) -> tuple[EntityCandidate, ...]:
    """Derive brand hints from catalog product rows (no hardcoded brand map)."""

    by_id: dict[str, EntityCandidate] = {}
    for row in pool:
        brand = None
        if row.get("brand"):
            brand = str(row["brand"]).strip()
        elif row.get("brand_model"):
            brand = str(row["brand_model"]).split("/")[0].strip()
        if not brand or len(brand) < 2:
            continue
        key = brand.casefold()
        if key in by_id:
            continue
        by_id[key] = EntityCandidate(
            entity_id=f"brand:{key}",
            display_name=brand,
            canonical_name=brand,
            aliases=(brand, key),
            entity_type="brand",
        )
    return tuple(by_id.values())


def categories_from_pool(
    pool: Sequence[dict[str, Any]],
    *,
    existing_codes: Optional[set[str]] = None,
) -> list[Category]:
    """Surface feed category labels as Category rows when DB catalog lags."""

    from taksitlio.product.taxonomy import taxonomy_code

    seen = set(existing_codes or ())
    out: list[Category] = []
    next_id = -1
    for row in pool:
        label = str(row.get("category") or "").strip()
        if not label:
            continue
        code = taxonomy_code(label)
        if code in seen:
            continue
        seen.add(code)
        out.append(
            Category(
                id=next_id,
                category_code=code,
                display_name=label[:128],
                description=f"Feed-derived category: {label}",
                synonyms=(label,),
            )
        )
        next_id -= 1
    return out


def institutions_from_labels(
    institutions: Optional[InstitutionLabelResolver],
) -> tuple[EntityCandidate, ...]:
    if institutions is None:
        return ()
    out: list[EntityCandidate] = []
    for iid, label in (institutions.labels or {}).items():
        name = str(label or iid).strip()
        if not name:
            continue
        out.append(
            EntityCandidate(
                entity_id=str(iid),
                display_name=name,
                canonical_name=name,
                aliases=(name, name.casefold()),
                entity_type="institution",
            )
        )
    return tuple(out)


def clarify_options_from_categories(
    categories: Sequence[Category],
    *,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Top-level clarify chips from active catalog (plus Diğer)."""

    options: list[dict[str, str]] = []
    for cat in categories:
        options.append({"id": str(cat.category_code), "label": cat.display_name})
        if len(options) >= limit:
            break
    options.append({"id": "other", "label": "Diğer"})
    return options[:4]


def apply_catalog_hints(
    orch: SearchOrchestrator,
    *,
    categories: Sequence[Category] = (),
    merchants: Sequence[EntityCandidate] = (),
    brands: Sequence[EntityCandidate] = (),
    institutions: Sequence[EntityCandidate] = (),
) -> None:
    """Replace CatalogHints + clarify options + token map from live sources."""

    from taksitlio.query_understanding.alias_index import build_alias_index

    cat_entities = tuple(category_to_entity(c) for c in categories)
    token_map = {str(c.category_code): category_include_tokens(c) for c in categories}
    merchant_t = tuple(merchants) if merchants else orch.catalog.merchants
    brand_t = tuple(brands) if brands else orch.catalog.brands
    inst_t = tuple(institutions) if institutions else orch.catalog.institutions
    cats_t = cat_entities if categories else orch.catalog.categories
    prev = orch.catalog
    reuse_index = (
        prev.alias_index is not None
        and prev.categories is cats_t
        and prev.merchants is merchant_t
        and prev.brands is brand_t
    )
    # Identity reuse when refresh keeps same cached tuples; else rebuild.
    if not reuse_index:
        reuse_index = (
            prev.alias_index is not None
            and len(prev.categories) == len(cats_t)
            and len(prev.merchants) == len(merchant_t)
            and len(prev.brands) == len(brand_t)
            and (not cats_t or prev.categories[0].entity_id == cats_t[0].entity_id)
            and (
                not cats_t
                or prev.categories[-1].entity_id == cats_t[-1].entity_id
            )
        )
    alias_index = (
        prev.alias_index
        if reuse_index
        else build_alias_index(
            categories=cats_t, merchants=merchant_t, brands=brand_t
        )
    )
    orch.catalog = CatalogHints(
        merchants=merchant_t,
        categories=cats_t,
        brands=brand_t,
        institutions=inst_t,
        alias_index=alias_index,
    )
    if categories:
        orch.category_clarify_options = clarify_options_from_categories(list(categories))
        orch.category_token_map = token_map
    elif not orch.category_clarify_options:
        orch.category_clarify_options = []


async def _load_hints(
    *,
    categories: Optional[CategoryListSource],
    merchants: Optional[MerchantDirectory],
) -> tuple[list[Category], list[EntityCandidate]]:
    global _inflight_hints
    now = time.monotonic()
    if (
        (now - float(_hints_cache["ts"])) < _HINTS_TTL_S
        and _hints_cache["categories"] is not None
    ):
        return list(_hints_cache["categories"]), list(_hints_cache["merchants"] or [])

    if _inflight_hints is not None and not _inflight_hints.done():
        return await _inflight_hints

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[list[Category], list[EntityCandidate]]] = loop.create_future()
    _inflight_hints = fut
    try:
        category_rows: list[Category] = []
        merchant_cands: list[EntityCandidate] = []
        if categories is not None:
            category_rows = list(await categories.list_active())
        if merchants is not None:
            for m in await merchants.list_active(limit=200):
                merchant_cands.append(
                    EntityCandidate(
                        entity_id=str(m.id),
                        display_name=m.display_name,
                        canonical_name=m.display_name,
                        aliases=(m.merchant_code, m.display_name),
                        entity_type="merchant",
                    )
                )
        _hints_cache["ts"] = time.monotonic()
        _hints_cache["categories"] = category_rows
        _hints_cache["merchants"] = merchant_cands
        _hints_cache["alias_index"] = None
        fut.set_result((category_rows, merchant_cands))
        return category_rows, merchant_cands
    except Exception as exc:  # noqa: BLE001
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        if _inflight_hints is fut:
            _inflight_hints = None


async def _hydrate_pool_rows(
    *,
    catalog: ProductCatalogRepository,
    merchants: Optional[MerchantDirectory],
    finance_index: Optional[FinanceOptionIndex],
    institutions: Optional[InstitutionLabelResolver],
    logos: Optional[LogoResolver],
    utterance: str,
    limit: int,
    prefer_search_ready: bool,
    pg_pool: Any,
    search_terms: Sequence[str],
    category_entities: Sequence[Any],
    alias_index: Any,
) -> list[dict[str, Any]]:
    pool_rows: list[dict[str, Any]] = []
    if prefer_search_ready:
        if pg_pool is not None:
            pool_rows = await _pool_rows_from_search_ready(
                pg_pool,
                name_terms=search_terms,
                limit=limit,
            )
            if not pool_rows:
                pool_rows = await _pool_rows_from_search_ready(
                    pg_pool,
                    name_terms=[],
                    limit=limit,
                )
        if logos is not None:
            for row in pool_rows:
                if not row.get("merchant_logo_cdn_url"):
                    row["merchant_logo_cdn_url"] = logos.merchant(row.get("merchant_id"))
        return pool_rows

    cands = await load_search_candidates_from_catalog(
        catalog,
        utterance=utterance,
        limit=limit,
        merchants=merchants,
        finance_index=finance_index,
        institutions=institutions,
        category_candidates=category_entities,
        alias_index=alias_index,
        name_terms=search_terms,
    )
    if cands:
        pool_rows = [candidate_to_pool_dict(c) for c in cands]
        if logos is not None:
            for row in pool_rows:
                if not row.get("merchant_logo_cdn_url"):
                    row["merchant_logo_cdn_url"] = logos.merchant(row.get("merchant_id"))
    return pool_rows


async def refresh_orchestrator_from_catalog(
    orch: SearchOrchestrator,
    *,
    catalog: ProductCatalogRepository,
    merchants: Optional[MerchantDirectory] = None,
    finance_index: Optional[FinanceOptionIndex] = None,
    institutions: Optional[InstitutionLabelResolver] = None,
    logos: Optional[LogoResolver] = None,
    categories: Optional[CategoryListSource] = None,
    utterance: str = "",
    limit: int = 80,
    prefer_search_ready: bool = False,
    pg_pool: Any = None,
    catalog_revision: str = "",
) -> int:
    """Load product pool + entity hints from production catalog sources.

    Concurrent cache-misses for the same revision-keyed pool coalesce via
    single-flight. Shared orchestrator apply is locked to avoid torn pools.
    """

    now = time.monotonic()
    category_rows, merchant_cands = await _load_hints(
        categories=categories, merchants=merchants
    )

    from taksitlio.query_understanding.alias_index import build_alias_index
    from taksitlio.progressive_results.category_match import utterance_name_terms

    category_entities = tuple(category_to_entity(c) for c in category_rows)
    alias_index = _hints_cache.get("alias_index")
    if alias_index is None:
        alias_index = build_alias_index(
            categories=category_entities, merchants=merchant_cands
        )
        _hints_cache["alias_index"] = alias_index
    search_terms = utterance_name_terms(
        utterance,
        category_candidates=category_entities or orch.catalog.categories,
        alias_index=alias_index,
    )
    pool_key = _pool_cache_key(
        utterance,
        limit,
        search_terms,
        catalog_revision=catalog_revision
        or str(getattr(orch, "catalog_revision", "") or ""),
        prefer_search_ready=prefer_search_ready,
    )
    cached = _pool_cache.get(pool_key)
    if cached and (now - cached[0]) < _POOL_TTL_S:
        pool_rows = [dict(r) for r in cached[1]]
    else:
        inflight = _inflight_pools.get(pool_key)
        if inflight is not None and not inflight.done():
            pool_rows = [dict(r) for r in await inflight]
        else:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
            _inflight_pools[pool_key] = fut
            try:
                loaded = await _hydrate_pool_rows(
                    catalog=catalog,
                    merchants=merchants,
                    finance_index=finance_index,
                    institutions=institutions,
                    logos=logos,
                    utterance=utterance,
                    limit=limit,
                    prefer_search_ready=prefer_search_ready,
                    pg_pool=pg_pool,
                    search_terms=search_terms,
                    category_entities=category_entities or orch.catalog.categories,
                    alias_index=alias_index,
                )
                _pool_cache[pool_key] = (
                    time.monotonic(),
                    [dict(r) for r in loaded],
                )
                if len(_pool_cache) > _POOL_CACHE_MAX:
                    for k, _ in sorted(_pool_cache.items(), key=lambda kv: kv[1][0])[
                        : len(_pool_cache) - _POOL_CACHE_MAX
                    ]:
                        _pool_cache.pop(k, None)
                fut.set_result(loaded)
                pool_rows = [dict(r) for r in loaded]
            except Exception as exc:  # noqa: BLE001
                if not fut.done():
                    fut.set_exception(exc)
                raise
            finally:
                _inflight_pools.pop(pool_key, None)

    if logos is not None:
        orch.logo_resolver = logos  # type: ignore[attr-defined]

    brand_cands = brands_from_pool(pool_rows)
    inst_cands = institutions_from_labels(institutions)
    existing_codes = {str(c.category_code) for c in category_rows}
    feed_cats = categories_from_pool(pool_rows, existing_codes=existing_codes)
    merged_categories = list(category_rows) + feed_cats

    async with _apply_lock:
        orch.product_pool = pool_rows
        apply_catalog_hints(
            orch,
            categories=merged_categories,
            merchants=merchant_cands,
            brands=brand_cands,
            institutions=inst_cands,
        )
    return len(pool_rows)


async def _pool_rows_from_search_ready(
    pg_pool: Any,
    *,
    name_terms: Sequence[str],
    limit: int,
) -> list[dict[str, Any]]:
    """INTERNAL fast path: cohort search-ready projection instead of full catalog scan."""

    terms = [str(t).strip() for t in name_terms if t and str(t).strip()][:8]
    try:
        async with pg_pool.acquire() as conn:
            if terms:
                clauses = " OR ".join(
                    f"(p.display_name ILIKE ${i + 2} OR p.normalized_name ILIKE ${i + 2} "
                    f"OR c.display_name ILIKE ${i + 2} OR c.category_code ILIKE ${i + 2})"
                    for i in range(len(terms))
                )
                params: list[Any] = [limit, *[f"%{t}%" for t in terms]]
                rows = await conn.fetch(
                    f"""
                    SELECT s.product_id, s.offer_id, s.merchant_id, s.category_id,
                           s.current_price, s.currency, s.stock_status, s.finance_ready,
                           p.display_name,
                           ma.cdn_url AS primary_cdn_url,
                           m.display_name AS merchant_display_name,
                           m.merchant_code,
                           c.category_code,
                           c.display_name AS category_display_name
                    FROM search_ready_product_projection s
                    JOIN products p ON p.id = s.product_id
                    JOIN merchants m ON m.id = s.merchant_id
                    LEFT JOIN media_assets ma ON ma.id = s.card_media_id
                    LEFT JOIN categories c ON c.id = s.category_id
                    WHERE ({clauses})
                    ORDER BY s.current_price ASC NULLS LAST
                    LIMIT $1
                    """,
                    *params,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT s.product_id, s.offer_id, s.merchant_id, s.category_id,
                           s.current_price, s.currency, s.stock_status, s.finance_ready,
                           p.display_name,
                           ma.cdn_url AS primary_cdn_url,
                           m.display_name AS merchant_display_name,
                           m.merchant_code,
                           c.category_code,
                           c.display_name AS category_display_name
                    FROM search_ready_product_projection s
                    JOIN products p ON p.id = s.product_id
                    JOIN merchants m ON m.id = s.merchant_id
                    LEFT JOIN media_assets ma ON ma.id = s.card_media_id
                    LEFT JOIN categories c ON c.id = s.category_id
                    ORDER BY s.updated_at DESC NULLS LAST
                    LIMIT $1
                    """,
                    limit,
                )
    except Exception:  # noqa: BLE001
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "product_id": str(r["product_id"]),
                "display_name": str(r["display_name"] or ""),
                "merchant_id": str(r["merchant_id"]),
                "merchant_display_name": str(r["merchant_display_name"] or ""),
                "merchant_code": str(r["merchant_code"] or ""),
                "price": float(r["current_price"] or 0),
                "currency": str(r["currency"] or "TRY"),
                "stock_status": str(r["stock_status"] or ""),
                "thumbnail_cdn_url": r["primary_cdn_url"],
                "category_id": str(r["category_id"]) if r["category_id"] is not None else None,
                "category_code": str(r["category_code"]) if r["category_code"] is not None else None,
                "category": str(r["category_display_name"] or r["category_code"] or "") or None,
                "product_type": str(r["category_code"]) if r["category_code"] is not None else None,
                "best_finance": None,
            }
        )
    return out
