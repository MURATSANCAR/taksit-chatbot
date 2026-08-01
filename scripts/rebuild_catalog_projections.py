#!/usr/bin/env python3
"""Rebuild searchable catalog projections (V027). Source catalog rows are not modified."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def _run(args: argparse.Namespace) -> int:
    dsn = args.database_url or os.environ.get("DATABASE_URL") or os.environ.get("PGVECTOR_URL")
    if not dsn:
        print("DATABASE_URL required", file=sys.stderr)
        return 2
    if not args.allow_write:
        print("Pass --allow-write to rebuild projection tables only.", file=sys.stderr)
        return 3

    import asyncpg
    from taksitlio.catalog_projection import CatalogProjectionRepository

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        stats = await CatalogProjectionRepository(pool).rebuild_all(
            catalog_revision=int(args.catalog_revision)
        )
    finally:
        await pool.close()

    print(json.dumps({"ok": True, "stats": stats.to_dict()}, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=None)
    p.add_argument("--catalog-revision", type=int, default=1)
    p.add_argument("--allow-write", action="store_true")
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
