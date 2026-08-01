#!/usr/bin/env python3
"""Feed source field probe for P3.2 (read-only)."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
feed = Path(os.environ.get("LIVE_FEED_DIR") or ROOT / "crawler" / "feeds" / "live")

for name in [
    "src-m-dr.json",
    "src-m-network.json",
    "src-m-civil.json",
    "src-m-evofone.json",
    "src-m-vivense.json",
    "src-m-teknosa.json",
    "src-m-koctas.json",
    "src-m-flo.json",
]:
    p = feed / name
    if not p.exists():
        print(name, "MISSING")
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    prods = data.get("products") or []
    sample = prods[: min(2000, len(prods))]
    cat = sum(1 for x in sample if x.get("category") or x.get("category_name"))
    brand = sum(1 for x in sample if x.get("brand"))
    attrs_cat = 0
    brand_attr = 0
    url_first: Counter[str] = Counter()
    keys: Counter[str] = Counter()
    for x in sample:
        a = x.get("attributes") if isinstance(x.get("attributes"), dict) else {}
        if a.get("category") or a.get("category_name"):
            attrs_cat += 1
        if (not x.get("brand")) and (a.get("brand") or a.get("Brand") or a.get("marka")):
            brand_attr += 1
        u = str(x.get("url") or x.get("product_url") or x.get("link") or "")
        for t in u.split("/"):
            if not t or t in ("https:", "http:") or "." in t:
                continue
            url_first[t] += 1
            break
        keys.update(a.keys())
    print(
        f"{name}: n={len(prods)} sample={len(sample)} "
        f"cat_field={cat} attrs_cat={attrs_cat} brand={brand} brand_attr={brand_attr}"
    )
    print("  top url first:", url_first.most_common(10))
    print("  top attr keys:", keys.most_common(12))
