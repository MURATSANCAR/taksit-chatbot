#!/usr/bin/env python3
import asyncio, os, sys
sys.path.insert(0, "src")

async def main():
    import asyncpg
    from taksitlio.catalog_events.emit import emit_events, events_for_product_upsert
    from taksitlio.product.catalog import PostgresProductCatalogRepository
    from taksitlio.product.upsert import ProductUpsertPlan
    from taksitlio.product.hashing import content_hash

    url = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    conn = await pool.acquire()
    before = await conn.fetchval("select count(*) from catalog_domain_events")
    print("before", before)
    ev = events_for_product_upsert(
        merchant_id=1,
        product_id=999999,
        external_product_id="p31-probe",
        content_hash="probe-hash-1",
        was_insert=True,
        source_id="probe",
        catalog_revision="r",
    )
    # direct emit
    stats = await emit_events(conn, ev)
    print("direct emit", stats)
    mid = await conn.fetchval("select id from merchants order by id limit 1")
    row = await conn.fetchrow(
        "select id, merchant_id, external_product_id, display_name, normalized_name, content_hash, data_quality_status, status, attributes, model_number, source_url, source_reference, short_description, full_description, merchant_sku, gtin, ean, mpn from products where status='ACTIVE' limit 1"
    )
    catalog = PostgresProductCatalogRepository(pool)
    attrs = row["attributes"] or {}
    if isinstance(attrs, str):
        import json
        attrs = json.loads(attrs)
    attrs = dict(attrs)
    attrs["_probe"] = "1"
    plan = ProductUpsertPlan(
        external_product_id=str(row["external_product_id"]),
        merchant_sku=row["merchant_sku"],
        gtin=row["gtin"],
        ean=row["ean"],
        mpn=row["mpn"],
        brand_name=None,
        category_name=None,
        model_number=row["model_number"],
        display_name=str(row["display_name"]),
        normalized_name=str(row["normalized_name"] or row["display_name"]),
        short_description=row["short_description"],
        full_description=row["full_description"],
        source_url=row["source_url"],
        content_hash=content_hash({"probe": attrs["_probe"], "id": row["id"]}),
        source_reference=row["source_reference"],
        attributes=attrs,
        canonical=None,
        action="UPSERT",
    )
    stored = await catalog.upsert_product(
        merchant_id=int(row["merchant_id"]),
        plan=plan,
        data_quality_status=str(row["data_quality_status"]),
        status=str(row["status"]),
    )
    print("upserted", stored.id)
    after = await conn.fetchval("select count(*) from catalog_domain_events")
    print("after", after, "delta", after - before)
    recent = await conn.fetch(
        "select event_type, processing_status from catalog_domain_events order by created_at desc limit 5"
    )
    print("recent", [dict(r) for r in recent])
    await pool.release(conn)
    await pool.close()

asyncio.run(main())
