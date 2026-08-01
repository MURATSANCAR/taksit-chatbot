#!/usr/bin/env python3
"""P3.5 latency span probe (INTERNAL API)."""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict
from urllib import request


def p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, int(round(0.95 * (len(s) - 1))))]


def main() -> None:
    token = (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip()
    base = (os.environ.get("TAKSITLIO_API_BASE") or "http://127.0.0.1:8040").rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Taksitlio-Traffic": "internal",
        "X-Taksitlio-Internal-Token": token,
        "X-Taksitlio-Cohort-Id": "1",
        "X-Taksitlio-Cohort-Version": "1",
    }
    queries = [
        "samsung telefon",
        "iphone",
        "laptop",
        "kulaklık",
        "televizyon",
        "tablet",
        "buzdolabı",
    ]
    span_durs: dict[str, list[float]] = defaultdict(list)
    totals: list[float] = []
    routes: dict[str, list[float]] = defaultdict(list)
    for i, q in enumerate(queries * 10):
        body = json.dumps(
            {"conversation_id": f"lat-{uuid.uuid4()}", "message": q}
        ).encode()
        req = request.Request(
            f"{base}/v1/search-sessions", data=body, headers=headers, method="POST"
        )
        t0 = time.perf_counter()
        with request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
        tot = (time.perf_counter() - t0) * 1000
        totals.append(tot)
        routes[str(data.get("route"))].append(tot)
        for s in (data.get("trace") or {}).get("spans") or []:
            span_durs[str(s["name"])].append(float(s["duration_ms"]))
    print(
        json.dumps(
            {
                "n": len(totals),
                "total_p50": sorted(totals)[len(totals) // 2],
                "total_p95": p95(totals),
                "routes": {
                    k: {"n": len(v), "p95": p95(v)} for k, v in routes.items()
                },
                "spans": [
                    {
                        "name": name,
                        "n": len(vals),
                        "mean": round(sum(vals) / len(vals), 3),
                        "p95": p95(vals),
                        "max": max(vals),
                    }
                    for name, vals in sorted(
                        span_durs.items(), key=lambda kv: -(p95(kv[1]) or 0)
                    )
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
