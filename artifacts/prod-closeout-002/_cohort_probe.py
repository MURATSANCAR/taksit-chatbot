#!/usr/bin/env python3
import asyncio, asyncpg, os, json, urllib.request, urllib.error

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch(
        """
        SELECT version, status, package_state, traffic_state,
               search_ready_product_count, finance_ready_product_count
        FROM search_release_cohort_versions
        WHERE cohort_id=1 ORDER BY version
        """
    )
    print("cohorts", json.dumps([dict(r) for r in rows], default=str))
    flags = await conn.fetch(
        "SELECT flag_code, status FROM runtime_feature_flags WHERE flag_code ILIKE '%internal%' OR flag_code ILIKE '%cohort%' OR flag_code ILIKE '%search%'"
    )
    print("flags", [dict(f) for f in flags])
    # try versions
    token = os.environ["TAKSITLIO_INTERNAL_TOKEN"]
    for ver in [1, 2, 3]:
        headers = {
            "Content-Type": "application/json",
            "X-Taksitlio-Traffic": "internal",
            "X-Taksitlio-Internal-Token": token,
            "X-Taksitlio-Cohort-Id": "1",
            "X-Taksitlio-Cohort-Version": str(ver),
        }
        req = urllib.request.Request(
            "http://127.0.0.1:8040/v1/search-sessions",
            data=json.dumps({"conversation_id": f"v{ver}", "message": "laptop"}).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode()
                print("ver", ver, "status", resp.status, "n", body.count("product_id"), body[:120])
        except urllib.error.HTTPError as e:
            print("ver", ver, "status", e.code, e.read()[:200])
    await conn.close()

asyncio.run(main())
